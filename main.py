"""
Orquestador Principal del Bot de Trading Algorítmico Multitemporal.
Integra Exchange, Estrategia, Gestión de Riesgo, Ejecución, Persistencia y Notificaciones en Telegram.
"""
import os
import sys
import time
import signal
import logging
import argparse
import threading
from datetime import datetime, timezone
import schedule
import colorlog
import uvicorn

from src.config_loader import load_config, AppConfig
from src.exchange_client import ExchangeClient
from src.strategy import MultiTimeframeStrategy
from src.risk_manager import RiskManager
from src.database import DatabaseManager
from src.notifier import TelegramNotifier
from src.order_executor import OrderExecutor
import src.web_server as web_server


def setup_logger() -> logging.Logger:
    """Configura el sistema de logging con colores y formato limpio."""
    log_format = "%(log_color)s%(asctime)s [%(levelname)s] %(name)s: %(message)s%(reset)s"
    formatter = colorlog.ColoredFormatter(
        log_format,
        datefmt="%Y-%m-%d %H:%M:%S",
        log_colors={
            "DEBUG": "cyan",
            "INFO": "green",
            "WARNING": "yellow",
            "ERROR": "red",
            "CRITICAL": "bold_red",
        }
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers = [handler]
    return logging.getLogger("CryptoBot")


class TradingBotOrchestrator:
    """
    Controlador central del ciclo de vida del bot de trading.
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self.logger = logging.getLogger("CryptoBot")
        self.is_running = True

        # Inicialización de subsistemas
        self.db = DatabaseManager(config.database)
        self.exchange_client = ExchangeClient(config.exchange, config.mode)
        self.strategy = MultiTimeframeStrategy(config.strategy)
        self.risk_manager = RiskManager(config.risk, config.mode, self.db)
        self.notifier = TelegramNotifier(config.telegram)
        self.order_executor = OrderExecutor(
            config=config,
            exchange_client=self.exchange_client,
            risk_manager=self.risk_manager,
            database=self.db,
            notifier=self.notifier
        )

        # Configuración de tareas programadas
        self._setup_schedules()

    def _setup_schedules(self) -> None:
        """Programa el reporte diario y el heartbeat periódico."""
        if self.config.telegram.send_heartbeat:
            interval = self.config.telegram.heartbeat_interval_minutes
            schedule.every(interval).minutes.do(self.send_heartbeat)
            self.logger.info(f"Heartbeat programado cada {interval} minutos.")

        daily_hour = self.config.telegram.daily_report_hour
        schedule.every().day.at(daily_hour).do(self.send_daily_report)
        self.logger.info(f"Reporte diario programado para las {daily_hour} UTC.")

    def run_iteration(self) -> None:
        """
        Ejecuta un ciclo completo de escaneo técnico y actualización de órdenes.
        """
        if not self.is_running:
            self.logger.debug("Bot en estado pausado. Saltando ciclo de trading.")
            return

        self.logger.info("--- Iniciando ciclo de análisis técnico ---")

        # 1. Actualizar posiciones activas
        self.order_executor.check_and_update_positions()

        # 2. Escanear símbolos
        for symbol in self.config.symbols:
            try:
                if symbol in self.order_executor.open_positions:
                    self.logger.debug(f"Saltando evaluación de entrada para {symbol} (posición ya abierta)")
                    continue

                df_1d = self.exchange_client.fetch_ohlcv(
                    symbol=symbol,
                    timeframe=self.config.strategy.macro.timeframe,
                    limit=self.config.strategy.macro.limit_candles
                )

                df_1h = self.exchange_client.fetch_ohlcv(
                    symbol=symbol,
                    timeframe=self.config.strategy.micro.timeframe,
                    limit=self.config.strategy.micro.limit_candles
                )

                current_price = self.exchange_client.fetch_ticker_price(symbol)
                signal_res = self.strategy.analyze(symbol, df_1d, df_1h, current_price)
                self.logger.info(f"[{symbol}] Precio: {current_price:.4f} | Acción: {signal_res.action} | {signal_res.reason}")

                if signal_res.action == "BUY":
                    self.logger.info(f"¡Señal BUY detectada para {symbol}! Intentando ejecutar orden...")
                    self.order_executor.execute_buy(
                        symbol=symbol,
                        current_price=current_price,
                        reason=signal_res.reason
                    )

            except Exception as e:
                self.logger.error(f"Error procesando símbolo {symbol}: {e}")

        self.logger.info(
            f"Ciclo finalizado. Posiciones abiertas: {len(self.order_executor.open_positions)} | "
            f"Valor Portafolio: {self.order_executor.get_total_portfolio_value():.2f} USDT"
        )

    def send_heartbeat(self) -> None:
        try:
            free_balance = self.exchange_client.get_free_balance()
            total_balance = self.order_executor.get_total_portfolio_value()
            open_pos_count = len(self.order_executor.open_positions)
            self.notifier.notify_heartbeat(
                open_positions=open_pos_count,
                free_balance=free_balance,
                total_balance=total_balance,
                is_dry_run=self.config.mode.dry_run
            )
        except Exception as e:
            self.logger.error(f"Error al enviar heartbeat: {e}")

    def send_daily_report(self) -> None:
        try:
            summary = self.db.get_trades_summary_today(is_dry_run=self.config.mode.dry_run)
            total_balance = self.order_executor.get_total_portfolio_value()
            free_balance = self.exchange_client.get_free_balance()
            open_count = len(self.order_executor.open_positions)

            self.notifier.notify_daily_report(
                summary=summary,
                current_balance=total_balance,
                is_dry_run=self.config.mode.dry_run
            )

            pnl_amount = summary.get("total_pnl", 0.0)
            pnl_percent = (pnl_amount / total_balance) * 100.0 if total_balance > 0 else 0.0
            self.db.record_daily_snapshot(
                total_balance=total_balance,
                free_balance=free_balance,
                open_trades_count=open_count,
                closed_trades_today=summary.get("count", 0),
                daily_pnl_amount=pnl_amount,
                daily_pnl_percent=pnl_percent,
                is_dry_run=self.config.mode.dry_run
            )
            self.logger.info("Reporte diario y snapshot registrados exitosamente.")
        except Exception as e:
            self.logger.error(f"Error generando reporte diario: {e}")

    def start_loop(self) -> None:
        """Bucle en segundo plano del orquestador."""
        interval = self.config.bot_loop.check_interval_seconds
        while True:
            try:
                if self.is_running:
                    self.run_iteration()
                    schedule.run_pending()
            except Exception as e:
                self.logger.critical(f"Error en el ciclo del bot: {e}", exc_info=True)

            time.sleep(interval)


def run_web_server(host: str = "0.0.0.0", port: int = 8000):
    """Ejecuta el servidor web FastAPI con Uvicorn."""
    config = uvicorn.Config(app=web_server.app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    server.run()


def main():
    parser = argparse.ArgumentParser(description="Bot de Trading Algorítmico Multitemporal con GUI Web")
    parser.add_argument("--config", default="config.yaml", help="Ruta al archivo config.yaml")
    parser.add_argument("--env", default=".env", help="Ruta al archivo .env")
    parser.add_argument("--port", type=int, default=8000, help="Puerto del Dashboard Web (default: 8000)")
    parser.add_argument("--no-gui", action="store_true", help="Desactivar interfaz gráfica web")
    parser.add_argument("--once", action="store_true", help="Ejecutar una sola iteración y finalizar")
    args = parser.parse_args()

    logger = setup_logger()

    try:
        cfg = load_config(config_path=args.config, env_path=args.env)
    except Exception as e:
        logger.critical(f"Error fatal al cargar configuración: {e}")
        sys.exit(1)

    bot = TradingBotOrchestrator(cfg)
    web_server.bot_orchestrator = bot

    mode_str = "SIMULACIÓN (DRY-RUN)" if cfg.mode.dry_run else "REAL MONEY"
    logger.info("=" * 60)
    logger.info(f"   BOT DE TRADING ALGORÍTMICO INICIADO - MODO: {mode_str}")
    logger.info(f"   Exchange: {cfg.exchange.name.upper()} | Símbolos: {cfg.symbols}")
    logger.info(f"   Balance Inicial: {bot.order_executor.get_total_portfolio_value():.2f} USDT")
    if not args.no_gui and not args.once:
        logger.info(f"   🌐 DASHBOARD WEB DISPONIBLE EN: http://localhost:{args.port}")
    logger.info("=" * 60)

    if args.once:
        logger.info("Modo ejecución única (--once) activado.")
        bot.run_iteration()
        return

    # Iniciar hilo de trading
    trading_thread = threading.Thread(target=bot.start_loop, daemon=True)
    trading_thread.start()

    # Ejecutar servidor web en el hilo principal
    if not args.no_gui:
        try:
            run_web_server(port=args.port)
        except (KeyboardInterrupt, SystemExit):
            logger.info("Servidor web detenido. Finalizando aplicación...")
    else:
        try:
            while True:
                time.sleep(1)
        except (KeyboardInterrupt, SystemExit):
            logger.info("Finalizando aplicación...")


if __name__ == "__main__":
    main()

