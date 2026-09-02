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
            sl_pct = t.get("stop_loss_percent") or self.config.risk.stop_loss_percent
            tp_pct = t.get("take_profit_percent") or self.config.risk.take_profit_percent
            sl_price, tp_price = self.risk_manager.calculate_initial_levels(t["entry_price"], custom_sl_percent=sl_pct, custom_tp_percent=tp_pct)
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
                opened_at=t["opened_at"],
                stop_loss_percent=sl_pct,
                take_profit_percent=tp_pct,
                trailing_stop_enabled=bool(t.get("trailing_stop_enabled", 1)),
                trailing_activation_percent=t.get("trailing_activation_percent") or self.config.risk.trailing_stop.activation_profit_percent,
                trailing_callback_percent=t.get("trailing_callback_percent") or self.config.risk.trailing_stop.callback_percent,
                enable_breakeven=bool(t.get("enable_breakeven", 1))
            )
            self.open_positions[t["symbol"]] = pos
        logger.info(f"Se cargaron {len(self.open_positions)} posiciones abiertas desde la base de datos.")

    def execute_buy(
        self,
        symbol: str,
        current_price: float,
        reason: str = "",
        custom_tp_percent: Optional[float] = None,
        custom_sl_percent: Optional[float] = None,
        quote_amount: Optional[float] = None,
        force: bool = False
    ) -> Optional[PositionState]:
        """
        Ejecuta una orden de compra (simulada o real) respetando la gestión de riesgo.
        """
        total_balance = self.get_total_portfolio_value()
        free_balance = self.client.get_free_balance()

        if symbol in self.open_positions:
            logger.info(f"Ya existe una posición abierta para {symbol}")
            return None

        if not force:
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

        if quote_amount is not None and quote_amount > 0 and quote_amount <= free_balance:
            cost = float(quote_amount)
            amount = cost / current_price
        else:
            cost, amount = self.risk_manager.calculate_position_size(free_balance, current_price)

        if cost <= 0 or amount <= 0 or cost > free_balance:
            logger.warning(f"Capital insuficiente para comprar {symbol}. Costo requerido: {cost:.2f}, Disponible: {free_balance:.2f}")
            return None

        sl_price, tp_price = self.risk_manager.calculate_initial_levels(
            current_price,
            custom_sl_percent=custom_sl_percent,
            custom_tp_percent=custom_tp_percent
        )
        fee_rate = (getattr(self.config.risk, "transaction_fee_percent", 0.1)) / 100.0
        estimated_fee = cost * fee_rate

        final_tp_pct = custom_tp_percent if custom_tp_percent is not None else self.config.risk.take_profit_percent
        final_sl_pct = custom_sl_percent if custom_sl_percent is not None else self.config.risk.stop_loss_percent

        trade_id: int = 0
        if self.is_dry_run:
            logger.info(f"[SIMULACIÓN] Compra ejecutada: {amount:.6f} {symbol} a {current_price:.4f} (Costo: {cost:.2f} USDT, Fee: {estimated_fee:.4f} USDT | TP: {final_tp_pct}%, SL: {final_sl_pct}%)")
            self.client.update_simulated_balance(-cost)
            trade_id = self.db.insert_trade_open(
                symbol=symbol,
                entry_price=current_price,
                amount=amount,
                cost=cost,
                fee=estimated_fee,
                is_dry_run=True,
                stop_loss_percent=final_sl_pct,
                take_profit_percent=final_tp_pct,
                trailing_stop_enabled=self.config.risk.trailing_stop.enabled,
                trailing_activation_percent=self.config.risk.trailing_stop.activation_profit_percent,
                trailing_callback_percent=self.config.risk.trailing_stop.callback_percent,
                enable_breakeven=self.config.risk.enable_breakeven
            )
        else:
            try:
                logger.info(f"[REAL] Ejecutando orden de compra de mercado para {symbol}: {amount:.6f}")
                order = self.client.client.create_market_buy_order(symbol, amount)
                exec_price = float(order.get("price") or current_price)
                exec_amount = float(order.get("amount") or amount)
                cost = exec_price * exec_amount
                estimated_fee = cost * fee_rate
                
                trade_id = self.db.insert_trade_open(
                    symbol=symbol,
                    entry_price=exec_price,
                    amount=exec_amount,
                    cost=cost,
                    fee=estimated_fee,
                    is_dry_run=False,
                    stop_loss_percent=final_sl_pct,
                    take_profit_percent=final_tp_pct,
                    trailing_stop_enabled=self.config.risk.trailing_stop.enabled,
                    trailing_activation_percent=self.config.risk.trailing_stop.activation_profit_percent,
                    trailing_callback_percent=self.config.risk.trailing_stop.callback_percent,
                    enable_breakeven=self.config.risk.enable_breakeven
                )
                current_price = exec_price
                amount = exec_amount
            except Exception as e:
                logger.error(f"Fallo al ejecutar orden de compra real para {symbol}: {e}")
                return None

        # Guardar en memoria con parámetros propios
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
            opened_at=datetime.now(timezone.utc).isoformat(),
            stop_loss_percent=final_sl_pct,
            take_profit_percent=final_tp_pct,
            trailing_stop_enabled=self.config.risk.trailing_stop.enabled,
            trailing_activation_percent=self.config.risk.trailing_stop.activation_profit_percent,
            trailing_callback_percent=self.config.risk.trailing_stop.callback_percent,
            enable_breakeven=self.config.risk.enable_breakeven
        )
        self.open_positions[symbol] = pos

        # Emitir notificación asíncrona a WebSockets para Toast en Frontend
        try:
            import src.web_server as web_server
            web_server.broadcast_trade_event({
                "action": "BUY",
                "symbol": symbol,
                "price": current_price,
                "amount": amount,
                "cost": cost,
                "fee": estimated_fee,
                "sl_price": sl_price,
                "tp_price": tp_price,
                "reason": reason,
                "is_dry_run": self.is_dry_run
            })
        except Exception as e:
            logger.debug(f"No se pudo emitir evento WS: {e}")

        # Enviar notificación a Telegram
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
        fee_rate = (getattr(self.config.risk, "transaction_fee_percent", 0.1)) / 100.0
        sell_fee = gross_return * fee_rate

        # PnL neto reduciendo comisión de compra + venta
        net_return = gross_return - sell_fee
        net_pnl_amount = pnl_amount - (pos.cost * fee_rate + sell_fee)
        net_pnl_percent = (net_pnl_amount / pos.cost) * 100.0 if pos.cost > 0 else pnl_percent

        if self.is_dry_run:
            logger.info(f"[SIMULACIÓN] Venta ejecutada para {symbol} a {current_price:.4f}. Razón: {exit_reason}, PnL Neto: {net_pnl_amount:+.2f} USDT ({net_pnl_percent:+.2f}%), Fee Venta: {sell_fee:.4f} USDT")
            self.client.update_simulated_balance(net_return)
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
            pnl_amount=net_pnl_amount,
            pnl_percent=net_pnl_percent,
            exit_reason=exit_reason,
            fee=sell_fee
        )

        # Emitir notificación a WebSockets para Toast emergente en Frontend
        try:
            import src.web_server as web_server
            web_server.broadcast_trade_event({
                "action": "SELL",
                "symbol": symbol,
                "entry_price": pos.entry_price,
                "exit_price": current_price,
                "amount": pos.amount,
                "pnl_amount": net_pnl_amount,
                "pnl_percent": net_pnl_percent,
                "exit_reason": exit_reason,
                "fee": sell_fee,
                "is_dry_run": self.is_dry_run
            })
        except Exception as e:
            logger.debug(f"No se pudo emitir evento WS de venta: {e}")

        # Notificar por Telegram
        self.notifier.notify_sell_order(
            symbol=symbol,
            entry_price=pos.entry_price,
            exit_price=current_price,
            pnl_amount=net_pnl_amount,
            pnl_percent=net_pnl_percent,
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

    def open_manual_position(
        self,
        symbol: str,
        quote_amount: Optional[float] = None,
        custom_tp_percent: Optional[float] = None,
        custom_sl_percent: Optional[float] = None,
        reason: str = "Apertura Manual / AI Copilot"
    ) -> Optional[PositionState]:
        """Abre inmediatamente una posición para un símbolo en específico."""
        try:
            current_price = self.client.fetch_ticker_price(symbol)
            if current_price <= 0:
                logger.warning(f"Precio inválido para {symbol}")
                return None

            # En simulación, si el saldo libre es insuficiente para la orden manual, auto-ajustar
            free_balance = self.client.get_free_balance()
            req_cost = quote_amount if quote_amount else (free_balance * (self.config.risk.position_size_percent / 100.0))
            if self.is_dry_run and req_cost > free_balance:
                self.client.update_simulated_balance(req_cost + 50.0)

            return self.execute_buy(
                symbol=symbol,
                current_price=current_price,
                reason=reason,
                custom_tp_percent=custom_tp_percent,
                custom_sl_percent=custom_sl_percent,
                quote_amount=quote_amount,
                force=True
            )
        except Exception as e:
            logger.error(f"Error abriendo posición manual para {symbol}: {e}")
            return None

    def close_position_by_symbol(self, symbol: str, reason: str = "CIERRE_MANUAL_AI") -> bool:
        """Cierra inmediatamente una posición abierta al precio actual de mercado."""
        pos = self.open_positions.get(symbol)
        if not pos:
            return False
        try:
            current_price = self.client.fetch_ticker_price(symbol)
            pnl_percent = ((current_price - pos.entry_price) / pos.entry_price) * 100.0
            pnl_amount = (current_price - pos.entry_price) * pos.amount
            return self.execute_sell(symbol, current_price, reason, pnl_amount, pnl_percent)
        except Exception as e:
            logger.error(f"Error cerrando posición {symbol}: {e}")
            return False

    def update_position_parameters(
        self,
        symbol: str,
        take_profit_percent: Optional[float] = None,
        stop_loss_percent: Optional[float] = None,
        trailing_activation_percent: Optional[float] = None,
        trailing_callback_percent: Optional[float] = None,
        enable_breakeven: Optional[bool] = None
    ) -> bool:
        """Modifica los parámetros individuales de una posición abierta en específico sin alterar a las demás."""
        pos = self.open_positions.get(symbol)
        if not pos:
            return False

        if take_profit_percent is not None and take_profit_percent > 0:
            pos.take_profit_percent = float(take_profit_percent)
            pos.tp_price = pos.entry_price * (1.0 + (pos.take_profit_percent / 100.0))

        if stop_loss_percent is not None and stop_loss_percent > 0:
            pos.stop_loss_percent = float(stop_loss_percent)
            pos.current_sl_price = pos.entry_price * (1.0 - (pos.stop_loss_percent / 100.0))

        if trailing_activation_percent is not None:
            pos.trailing_activation_percent = float(trailing_activation_percent)

        if trailing_callback_percent is not None:
            pos.trailing_callback_percent = float(trailing_callback_percent)

        if enable_breakeven is not None:
            pos.enable_breakeven = bool(enable_breakeven)

        # Guardar en base de datos SQLite
        self.db.update_trade_parameters(
            trade_id=pos.trade_id,
            stop_loss_percent=pos.stop_loss_percent,
            take_profit_percent=pos.take_profit_percent,
            trailing_stop_enabled=pos.trailing_stop_enabled,
            trailing_activation_percent=pos.trailing_activation_percent,
            trailing_callback_percent=pos.trailing_callback_percent,
            enable_breakeven=pos.enable_breakeven
        )
        logger.info(f"[{symbol}] Parámetros de posición actualizados aisladamente: TP={pos.take_profit_percent}%, SL={pos.stop_loss_percent}%")
        return True
