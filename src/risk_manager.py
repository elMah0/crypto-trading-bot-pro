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
            return False, f"Límite de posiciones concurrentes alcanzado ({len(current_open_positions)}/{self.config.max_concurrent_trades})"

        # 3. Cortocircuito por pérdida diaria en 24h
        pnl_24h = self.db.get_pnl_last_24h(is_dry_run=self.mode.dry_run)
        if total_balance > 0 and pnl_24h < 0:
            loss_percent = (abs(pnl_24h) / total_balance) * 100.0
            if loss_percent >= self.config.circuit_breaker.max_daily_loss_percent:
                return False, f"Cortocircuito ACTIVO: Pérdida en 24h de {loss_percent:.2f}% superó el máximo de {self.config.circuit_breaker.max_daily_loss_percent}%"

        # 4. Enfriamiento por Stop Loss reciente en el mismo símbolo
        last_trade = self.db.get_last_closed_trade_for_symbol(symbol, is_dry_run=self.mode.dry_run)
        if last_trade and last_trade.get("exit_reason") == "STOP_LOSS":
            closed_at_str = last_trade.get("closed_at")
            if closed_at_str:
                try:
                    closed_at = datetime.fromisoformat(closed_at_str)
                    cooldown_delta = timedelta(hours=self.config.circuit_breaker.sl_cooldown_hours)
                    now_utc = datetime.now(timezone.utc)
                    if now_utc < closed_at + cooldown_delta:
                        time_remaining = (closed_at + cooldown_delta) - now_utc
                        mins_left = int(time_remaining.total_seconds() / 60)
                        return False, f"Periodo de enfriamiento (Cooldown) activo para {symbol} por SL previo ({mins_left} min restantes)"
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
        Permite sobrescribir con niveles dinámicos optimizados por la IA.
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
        Monitorea el precio actual frente a una posición abierta.
        Actualiza el Trailing Stop si corresponde y determina si se debe cerrar.
        Retorna (exit_reason, pnl_amount, pnl_percent) o (None, pnl_amount, pnl_percent) si se mantiene abierta.
        """
        pnl_percent = ((current_price - position.entry_price) / position.entry_price) * 100.0
        pnl_amount = (current_price - position.entry_price) * position.amount

        # 1. Verificar si alcanza nuevo máximo y actualizar Trailing Stop
        if current_price > position.highest_price:
            position.highest_price = current_price

        # 2. Lógica de Trailing Stop y Breakeven dinámico
        ts_cfg = self.config.trailing_stop
        fee_percent = getattr(self.config, "transaction_fee_percent", 0.1)

        # Breakeven: Si la posición alcanza ganancia suficiente para cubrir comisiones de ida y vuelta + 0.1% libre, proteger entrada
        if getattr(self.config, "enable_breakeven", True):
            breakeven_trigger = (fee_percent * 2.0) + 0.1
            if pnl_percent >= breakeven_trigger:
                breakeven_sl = position.entry_price * (1.0 + ((fee_percent * 2.0) / 100.0))
                if breakeven_sl > position.current_sl_price:
                    position.current_sl_price = breakeven_sl
                    logger.info(f"[{position.symbol}] Stop Loss ajustado a BREAKEVEN + COMISIONES ({breakeven_sl:.4f})")

        if ts_cfg.enabled:
            # Ganancia desde la entrada requerida para activar
            current_max_profit_percent = ((position.highest_price - position.entry_price) / position.entry_price) * 100.0
            if not position.trailing_active and current_max_profit_percent >= ts_cfg.activation_profit_percent:
                position.trailing_active = True
                logger.info(f"[{position.symbol}] Trailing Stop ACTIVADO con ganancia de {current_max_profit_percent:.2f}%")

            if position.trailing_active:
                # El nuevo SL se sitúa al callback_percent por debajo del precio más alto
                dynamic_sl = position.highest_price * (1.0 - (ts_cfg.callback_percent / 100.0))
                if dynamic_sl > position.current_sl_price:
                    position.current_sl_price = dynamic_sl

        # 3. Comprobación de salidas
        # A) Take Profit fijo
        if current_price >= position.tp_price:
            return "TAKE_PROFIT", pnl_amount, pnl_percent

        # B) Trailing Stop alcanzado
        if position.trailing_active and current_price <= position.current_sl_price:
            return "TRAILING_STOP", pnl_amount, pnl_percent

        # C) Stop Loss / Breakeven alcanzado
        if current_price <= position.current_sl_price:
            exit_name = "BREAKEVEN" if position.current_sl_price > position.entry_price else "STOP_LOSS"
            return exit_name, pnl_amount, pnl_percent

        return None, pnl_amount, pnl_percent

