"""
Módulo del Ejecutor de Órdenes (Order Executor).
Gestiona la ejecución de compras/ventas tanto en modo real con CCXT como en modo simulación (Dry-Run).
"""
import logging
from typing import Dict, Optional, Any
from datetime import datetime, timezone
import ccxt

from .config_loader import AppConfig
from .exchange_client import ExchangeClient
from .risk_manager import RiskManager, PositionState
from .database import DatabaseManager
from .notifier import TelegramNotifier

logger = logging.getLogger(__name__)


class OrderExecutor:
    """
    Coordina la compra y venta de activos, manteniendo el estado en memoria,
    actualizando la base de datos SQLite y enviando notificaciones a Telegram.
    """

    def __init__(
        self,
        config: AppConfig,
        exchange_client: ExchangeClient,
        risk_manager: RiskManager,
        database: DatabaseManager,
        notifier: TelegramNotifier
    ):
        self.config = config
        self.client = exchange_client
        self.risk_manager = risk_manager
        self.db = database
        self.notifier = notifier
        self.is_dry_run = config.mode.dry_run

        # Estado en memoria de posiciones abiertas indexadas por símbolo
        self.open_positions: Dict[str, PositionState] = {}
        self._load_existing_positions()

    def _load_existing_positions(self) -> None:
        """Carga posiciones abiertas existentes desde la base de datos al iniciar."""
        trades = self.db.get_open_trades(is_dry_run=self.is_dry_run)
        for t in trades:
            sl_price, tp_price = self.risk_manager.calculate_initial_levels(t["entry_price"])
            pos = PositionState(
                trade_id=t["id"],
                symbol=t["symbol"],
                entry_price=t["entry_price"],
                amount=t["amount"],
                cost=t["cost"],
                highest_price=t["entry_price"],
                trailing_active=False,
                current_sl_price=sl_price,
                tp_price=tp_price,
                opened_at=t["opened_at"]
            )
            self.open_positions[t["symbol"]] = pos
        logger.info(f"Se cargaron {len(self.open_positions)} posiciones abiertas desde la base de datos.")

    def execute_buy(self, symbol: str, current_price: float, reason: str) -> Optional[PositionState]:
        """
        Ejecuta una orden de compra respetando la gestión de riesgo.
        Soporta modo Dry-Run o ejecución real a través de CCXT.
        """
        total_balance = self.get_total_portfolio_value()
        free_balance = self.client.get_free_balance()

        can_open, risk_reason = self.risk_manager.can_open_position(
            symbol=symbol,
            current_open_positions=self.open_positions,
            total_balance=total_balance
        )

        if not can_open:
            logger.info(f"Compra denegada para {symbol} por gestión de riesgo: {risk_reason}")
            if "Cortocircuito" in risk_reason:
                self.notifier.notify_circuit_breaker(risk_reason)
            return None

        cost, amount = self.risk_manager.calculate_position_size(free_balance, current_price)
        if cost <= 0 or amount <= 0 or cost > free_balance:
            logger.warning(f"Capital insuficiente para comprar {symbol}. Costo requerido: {cost:.2f}, Disponible: {free_balance:.2f}")
            return None

        sl_price, tp_price = self.risk_manager.calculate_initial_levels(current_price)
        estimated_fee = cost * 0.001  # Estimación 0.1% de comisión estándar

        trade_id: int = 0
        if self.is_dry_run:
            logger.info(f"[SIMULACIÓN] Compra ejecutada: {amount:.6f} {symbol} a {current_price:.4f} (Costo: {cost:.2f} USDT)")
            self.client.update_simulated_balance(-cost)
            trade_id = self.db.insert_trade_open(
                symbol=symbol,
                entry_price=current_price,
                amount=amount,
                cost=cost,
                fee=estimated_fee,
                is_dry_run=True
            )
        else:
            try:
                logger.info(f"[REAL] Ejecutando orden de compra de mercado para {symbol}: {amount:.6f}")
                order = self.client.client.create_market_buy_order(symbol, amount)
                exec_price = float(order.get("price") or current_price)
                exec_amount = float(order.get("amount") or amount)
                cost = exec_price * exec_amount
                
                trade_id = self.db.insert_trade_open(
                    symbol=symbol,
                    entry_price=exec_price,
                    amount=exec_amount,
                    cost=cost,
                    fee=estimated_fee,
                    is_dry_run=False
                )
                current_price = exec_price
                amount = exec_amount
            except Exception as e:
                logger.error(f"Fallo al ejecutar orden de compra real para {symbol}: {e}")
                return None

        # Guardar en memoria
        pos = PositionState(
            trade_id=trade_id,
            symbol=symbol,
            entry_price=current_price,
            amount=amount,
            cost=cost,
            highest_price=current_price,
            trailing_active=False,
            current_sl_price=sl_price,
            tp_price=tp_price,
            opened_at=datetime.now(timezone.utc).isoformat()
        )
        self.open_positions[symbol] = pos

        # Enviar notificación audible a Telegram
        self.notifier.notify_buy_order(
            symbol=symbol,
            price=current_price,
            amount=amount,
            cost=cost,
            sl_price=sl_price,
            tp_price=tp_price,
            is_dry_run=self.is_dry_run,
            reason=reason
        )

        return pos

    def execute_sell(
        self,
        symbol: str,
        current_price: float,
        exit_reason: str,
        pnl_amount: float,
        pnl_percent: float
    ) -> bool:
        """
        Ejecuta la venta y cierre de una posición activa.
        """
        pos = self.open_positions.get(symbol)
        if not pos:
            logger.warning(f"Intento de cerrar posición inexistente para {symbol}")
            return False

        gross_return = pos.amount * current_price
        fee = gross_return * 0.001

        if self.is_dry_run:
            logger.info(f"[SIMULACIÓN] Venta ejecutada para {symbol} a {current_price:.4f}. Razón: {exit_reason}, PnL: {pnl_amount:+.2f} USDT ({pnl_percent:+.2f}%)")
            self.client.update_simulated_balance(gross_return)
        else:
            try:
                logger.info(f"[REAL] Ejecutando orden de venta de mercado para {symbol}: {pos.amount:.6f}")
                self.client.client.create_market_sell_order(symbol, pos.amount)
            except Exception as e:
                logger.error(f"Fallo crítico al ejecutar orden de venta real para {symbol}: {e}")
                return False

        # Actualizar base de datos
        self.db.close_trade(
            trade_id=pos.trade_id,
            exit_price=current_price,
            pnl_amount=pnl_amount,
            pnl_percent=pnl_percent,
            exit_reason=exit_reason,
            fee=fee
        )

        # Notificar por Telegram
        self.notifier.notify_sell_order(
            symbol=symbol,
            entry_price=pos.entry_price,
            exit_price=current_price,
            pnl_amount=pnl_amount,
            pnl_percent=pnl_percent,
            exit_reason=exit_reason,
            is_dry_run=self.is_dry_run
        )

        # Eliminar del estado en memoria
        del self.open_positions[symbol]
        return True

    def check_and_update_positions(self) -> None:
        """
        Escanea todas las posiciones abiertas, actualiza su trailing stop y gatilla salidas si aplica.
        """
        active_symbols = list(self.open_positions.keys())
        for symbol in active_symbols:
            pos = self.open_positions[symbol]
            try:
                current_price = self.client.fetch_ticker_price(symbol)
                exit_reason, pnl_amount, pnl_percent = self.risk_manager.evaluate_position_exit(pos, current_price)

                if exit_reason:
                    logger.info(f"[{symbol}] Condición de salida activada: {exit_reason} a precio {current_price:.4f}")
                    self.execute_sell(
                        symbol=symbol,
                        current_price=current_price,
                        exit_reason=exit_reason,
                        pnl_amount=pnl_amount,
                        pnl_percent=pnl_percent
                    )
            except Exception as e:
                logger.error(f"Error actualizando posición para {symbol}: {e}")

    def get_total_portfolio_value(self) -> float:
        """Calcula el valor total del portafolio (balance libre + valor actual de posiciones abiertas)."""
        free_balance = self.client.get_free_balance()
        positions_value = 0.0

        for symbol, pos in self.open_positions.items():
            try:
                cur_price = self.client.fetch_ticker_price(symbol)
                positions_value += (pos.amount * cur_price)
            except Exception:
                positions_value += pos.cost

        return free_balance + positions_value
