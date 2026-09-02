"""
Módulo de Gestión de Riesgo (Risk Manager).
Gestiona el tamaño de posición, Trailing Stop Loss dinámico, Stop Loss / Take Profit fijos y Cortocircuitos.
"""
import logging
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple

from .config_loader import RiskConfig, ModeConfig
from .database import DatabaseManager

logger = logging.getLogger(__name__)


@dataclass
class PositionState:
    trade_id: int
    symbol: str
    entry_price: float
    amount: float
    cost: float
    highest_price: float
    trailing_active: bool
    current_sl_price: float
    tp_price: float
    opened_at: str
    # Parámetros individuales e independientes por posición (opcionales, heredan del template si son None)
    stop_loss_percent: Optional[float] = None
    take_profit_percent: Optional[float] = None
    trailing_stop_enabled: Optional[bool] = None
    trailing_activation_percent: Optional[float] = None
    trailing_callback_percent: Optional[float] = None
    enable_breakeven: Optional[bool] = None


class RiskManager:
    """
    Controlador central de riesgo:
    - Valida si se puede abrir una nueva posición respetando límites de concurrencia y cortocircuitos.
    - Calcula el tamaño de la orden (% de capital).
    - Monitorea posiciones abiertas y calcula señales de salida (SL, TP, Trailing Stop).
    """

    def __init__(self, risk_config: RiskConfig, mode_config: ModeConfig, db: DatabaseManager):
        self.config = risk_config
        self.mode = mode_config
        self.db = db

    def can_open_position(
        self,
        symbol: str,
        current_open_positions: Dict[str, PositionState],
        total_balance: float
    ) -> Tuple[bool, str]:
        """
        Verifica todas las condiciones de seguridad antes de permitir una compra:
        1. Si ya existe una posición abierta para este símbolo.
        2. Límite de posiciones concurrentes.
        3. Cortocircuito por pérdida máxima en 24h.
        4. Periodo de enfriamiento (cooldown) tras Stop Loss en este símbolo.
        """
        # 1. Ya en posición
        if symbol in current_open_positions:
            return False, f"Ya existe una posición abierta para {symbol}"

        # 2. Concurrencia máxima
        if len(current_open_positions) >= self.config.max_concurrent_trades:
            return False, f"Límite máximo de posiciones concurrentes alcanzado ({self.config.max_concurrent_trades})"

        # 3. Cortocircuito de pérdida diaria
        cb_cfg = self.config.circuit_breaker
        summary_today = self.db.get_trades_summary_today(is_dry_run=self.mode.dry_run)
        daily_loss = summary_today.get("total_loss", 0.0)

        if total_balance > 0:
            daily_loss_percent = (daily_loss / total_balance) * 100.0
            if daily_loss_percent >= cb_cfg.max_daily_loss_percent:
                return False, f"Cortocircuito activado: Pérdida diaria ({daily_loss_percent:.2f}%) supera el máximo ({cb_cfg.max_daily_loss_percent}%)"

        # 4. Cooldown tras Stop Loss
        last_trade = self.db.get_last_trade(symbol, is_dry_run=self.mode.dry_run)
        if last_trade and last_trade.get("exit_reason") == "STOP_LOSS":
            closed_at_str = last_trade.get("closed_at")
            if closed_at_str:
                try:
                    closed_at = datetime.fromisoformat(closed_at_str)
                    now_utc = datetime.now(timezone.utc)
                    cooldown_delta = timedelta(hours=cb_cfg.sl_cooldown_hours)
                    if now_utc - closed_at < cooldown_delta:
                        remaining = cooldown_delta - (now_utc - closed_at)
                        hours_rem = remaining.total_seconds() / 3600.0
                        return False, f"Símbolo {symbol} en periodo de cooldown tras SL (Restan {hours_rem:.1f} horas)"
                except Exception as e:
                    logger.warning(f"Error parseando closed_at para cooldown de {symbol}: {e}")

        return True, "Condiciones de riesgo favorables para operar"

    def calculate_position_size(self, free_balance: float, current_price: float) -> Tuple[float, float]:
        """
        Calcula el capital a asignar y la cantidad de tokens a comprar.
        Retorna (cost_in_quote, amount_in_base).
        """
        if free_balance <= 0 or current_price <= 0:
            return 0.0, 0.0

        allocated_capital = free_balance * (self.config.position_size_percent / 100.0)
        amount = allocated_capital / current_price
        return allocated_capital, amount

    def calculate_initial_levels(
        self,
        entry_price: float,
        custom_sl_percent: Optional[float] = None,
        custom_tp_percent: Optional[float] = None
    ) -> Tuple[float, float]:
        """
        Calcula los precios de Stop Loss inicial y Take Profit inicial.
        Permite sobrescribir con niveles dinámicos optimizados por la IA o por el usuario.
        Retorna (sl_price, tp_price).
        """
        sl_pct = custom_sl_percent if custom_sl_percent is not None and custom_sl_percent > 0 else self.config.stop_loss_percent
        tp_pct = custom_tp_percent if custom_tp_percent is not None and custom_tp_percent > 0 else self.config.take_profit_percent

        sl_price = entry_price * (1.0 - (sl_pct / 100.0))
        tp_price = entry_price * (1.0 + (tp_pct / 100.0))
        return sl_price, tp_price

    def evaluate_position_exit(
        self,
        position: PositionState,
        current_price: float
    ) -> Tuple[Optional[str], float, float]:
        """
        Monitorea el precio actual frente a una posición abierta utilizando SUS PROPIOS PARÁMETROS individuales.
        Actualiza el Trailing Stop si corresponde y determina si se debe cerrar.
        Retorna (exit_reason, pnl_amount, pnl_percent) o (None, pnl_amount, pnl_percent) si se mantiene abierta.
        """
        pnl_percent = ((current_price - position.entry_price) / position.entry_price) * 100.0
        pnl_amount = (current_price - position.entry_price) * position.amount

        # 1. Verificar si alcanza nuevo máximo y actualizar Trailing Stop
        if current_price > position.highest_price:
            position.highest_price = current_price

        # 2. Lógica de Trailing Stop y Breakeven individual
        fee_percent = getattr(self.config, "transaction_fee_percent", 0.1)

        # Breakeven individual
        is_breakeven_enabled = position.enable_breakeven if position.enable_breakeven is not None else getattr(self.config, "enable_breakeven", True)
        if is_breakeven_enabled:
            breakeven_trigger = (fee_percent * 2.0) + 0.1
            if pnl_percent >= breakeven_trigger:
                breakeven_sl = position.entry_price * (1.0 + ((fee_percent * 2.0) / 100.0))
                if breakeven_sl > position.current_sl_price:
                    position.current_sl_price = breakeven_sl
                    logger.info(f"[{position.symbol}] Stop Loss ajustado a BREAKEVEN + COMISIONES ({breakeven_sl:.4f})")

        # Trailing Stop individual
        is_ts_enabled = position.trailing_stop_enabled if position.trailing_stop_enabled is not None else self.config.trailing_stop.enabled
        if is_ts_enabled:
            act_pct = position.trailing_activation_percent if position.trailing_activation_percent is not None else self.config.trailing_stop.activation_profit_percent
            cb_pct = position.trailing_callback_percent if position.trailing_callback_percent is not None else self.config.trailing_stop.callback_percent

            current_max_profit_percent = ((position.highest_price - position.entry_price) / position.entry_price) * 100.0
            if not position.trailing_active and current_max_profit_percent >= act_pct:
                position.trailing_active = True
                logger.info(f"[{position.symbol}] Trailing Stop individual ACTIVADO con ganancia de {current_max_profit_percent:.2f}%")

            if position.trailing_active:
                dynamic_sl = position.highest_price * (1.0 - (cb_pct / 100.0))
                if dynamic_sl > position.current_sl_price:
                    position.current_sl_price = dynamic_sl

        # 3. Comprobación de salidas
        # A) Take Profit individual
        if current_price >= position.tp_price:
            return "TAKE_PROFIT", pnl_amount, pnl_percent

        # B) Trailing Stop individual alcanzado
        if position.trailing_active and current_price <= position.current_sl_price:
            return "TRAILING_STOP", pnl_amount, pnl_percent

        # C) Stop Loss individual / Breakeven alcanzado
        if current_price <= position.current_sl_price:
            exit_name = "BREAKEVEN" if position.current_sl_price > position.entry_price else "STOP_LOSS"
            return exit_name, pnl_amount, pnl_percent

        return None, pnl_amount, pnl_percent
