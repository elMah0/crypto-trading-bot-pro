"""
Módulo de carga y validación de configuración (YAML y Variables de Entorno).
"""
import os
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


@dataclass
class ModeConfig:
    dry_run: bool = True
    initial_simulated_balance: float = 1000.0
    quote_currency: str = "USDT"


@dataclass
class ExchangeConfig:
    name: str = "binance"
    testnet: bool = False
    rate_limit_delay_ms: int = 250
    timeout_seconds: int = 30
    max_retries: int = 3
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    password: Optional[str] = None


@dataclass
class MacroStrategyConfig:
    timeframe: str = "1d"
    limit_candles: int = 30
    sma_period: int = 10
    adx_period: int = 14
    adx_min_strength: float = 20.0


@dataclass
class MicroStrategyConfig:
    timeframe: str = "1h"
    limit_candles: int = 60
    rsi_period: int = 14
    rsi_max_entry: float = 62.0
    rsi_min_entry: float = 30.0
    volume_sma_period: int = 10
    volume_factor: float = 0.85



@dataclass
class StrategyConfig:
    macro: MacroStrategyConfig = field(default_factory=MacroStrategyConfig)
    micro: MicroStrategyConfig = field(default_factory=MicroStrategyConfig)


@dataclass
class TrailingStopConfig:
    enabled: bool = True
    activation_profit_percent: float = 1.5
    callback_percent: float = 1.0


@dataclass
class CircuitBreakerConfig:
    max_daily_loss_percent: float = 5.0
    sl_cooldown_hours: int = 4


@dataclass
class RiskConfig:
    position_size_percent: float = 10.0
    max_concurrent_trades: int = 3
    stop_loss_percent: float = 2.0
    take_profit_percent: float = 4.0
    trailing_stop: TrailingStopConfig = field(default_factory=TrailingStopConfig)
    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)


@dataclass
class TelegramConfig:
    enabled: bool = True
    send_heartbeat: bool = True
    heartbeat_interval_minutes: int = 60
    daily_report_hour: str = "20:00"
    bot_token: Optional[str] = None
    chat_id: Optional[str] = None


@dataclass
class DatabaseConfig:
    db_path: str = "trading_bot.db"


@dataclass
class BotLoopConfig:
    check_interval_seconds: int = 60


@dataclass
class AppConfig:
    mode: ModeConfig = field(default_factory=ModeConfig)
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    symbols: List[str] = field(default_factory=lambda: ["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    bot_loop: BotLoopConfig = field(default_factory=BotLoopConfig)


def load_config(config_path: str = "config.yaml", env_path: Optional[str] = ".env") -> AppConfig:
    """
    Carga y valida el archivo config.yaml integrando las variables de entorno de .env.
    """
    # Cargar variables de entorno si existe el archivo .env o del sistema
    if env_path and os.path.exists(env_path):
        load_dotenv(env_path)
    else:
        load_dotenv()

    raw_cfg: Dict[str, Any] = {}
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            try:
                raw_cfg = yaml.safe_load(f) or {}
            except yaml.YAMLError as e:
                logger.error(f"Error parseando el archivo YAML de configuración '{config_path}': {e}")
                raise
    else:
        logger.warning(f"Archivo de configuración '{config_path}' no encontrado. Usando valores predeterminados.")

    # 1. Mode Config
    mode_raw = raw_cfg.get("mode", {})
    mode_cfg = ModeConfig(
        dry_run=bool(mode_raw.get("dry_run", True)),
        initial_simulated_balance=float(mode_raw.get("initial_simulated_balance", 1000.0)),
        quote_currency=str(mode_raw.get("quote_currency", "USDT")).upper()
    )

    # 2. Exchange Config & Environment Overrides
    exc_raw = raw_cfg.get("exchange", {})
    api_key = os.getenv("EXCHANGE_API_KEY") or exc_raw.get("api_key")
    api_secret = os.getenv("EXCHANGE_API_SECRET") or exc_raw.get("api_secret")
    password = os.getenv("EXCHANGE_PASSWORD") or exc_raw.get("password")

    exc_cfg = ExchangeConfig(
        name=str(exc_raw.get("name", "binance")).lower(),
        testnet=bool(exc_raw.get("testnet", False)),
        rate_limit_delay_ms=int(exc_raw.get("rate_limit_delay_ms", 250)),
        timeout_seconds=int(exc_raw.get("timeout_seconds", 30)),
        max_retries=int(exc_raw.get("max_retries", 3)),
        api_key=api_key,
        api_secret=api_secret,
        password=password
    )

    # 3. Symbols
    symbols = raw_cfg.get("symbols", ["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    if not isinstance(symbols, list) or len(symbols) == 0:
        raise ValueError("La lista de símbolos ('symbols') no puede estar vacía.")

    # 4. Strategy Config
    strat_raw = raw_cfg.get("strategy", {})
    macro_raw = strat_raw.get("macro", {})
    micro_raw = strat_raw.get("micro", {})

    macro_cfg = MacroStrategyConfig(
        timeframe=str(macro_raw.get("timeframe", "1d")),
        limit_candles=int(macro_raw.get("limit_candles", 30)),
        sma_period=int(macro_raw.get("sma_period", 10)),
        adx_period=int(macro_raw.get("adx_period", 14)),
        adx_min_strength=float(macro_raw.get("adx_min_strength", 20.0))
    )

    micro_cfg = MicroStrategyConfig(
        timeframe=str(micro_raw.get("timeframe", "1h")),
        limit_candles=int(micro_raw.get("limit_candles", 60)),
        rsi_period=int(micro_raw.get("rsi_period", 14)),
        rsi_max_entry=float(micro_raw.get("rsi_max_entry", 62.0)),
        rsi_min_entry=float(micro_raw.get("rsi_min_entry", 30.0)),
        volume_sma_period=int(micro_raw.get("volume_sma_period", 10)),
        volume_factor=float(micro_raw.get("volume_factor", 0.85))
    )

    strat_cfg = StrategyConfig(macro=macro_cfg, micro=micro_cfg)

    # 5. Risk Config
    risk_raw = raw_cfg.get("risk", {})
    ts_raw = risk_raw.get("trailing_stop", {})
    cb_raw = risk_raw.get("circuit_breaker", {})

    ts_cfg = TrailingStopConfig(
        enabled=bool(ts_raw.get("enabled", True)),
        activation_profit_percent=float(ts_raw.get("activation_profit_percent", 1.5)),
        callback_percent=float(ts_raw.get("callback_percent", 1.0))
    )

    cb_cfg = CircuitBreakerConfig(
        max_daily_loss_percent=float(cb_raw.get("max_daily_loss_percent", 5.0)),
        sl_cooldown_hours=int(cb_raw.get("sl_cooldown_hours", 4))
    )

    risk_cfg = RiskConfig(
        position_size_percent=float(risk_raw.get("position_size_percent", 10.0)),
        max_concurrent_trades=int(risk_raw.get("max_concurrent_trades", 3)),
        stop_loss_percent=float(risk_raw.get("stop_loss_percent", 2.0)),
        take_profit_percent=float(risk_raw.get("take_profit_percent", 4.0)),
        trailing_stop=ts_cfg,
        circuit_breaker=cb_cfg
    )

    # 6. Telegram Config
    tg_raw = raw_cfg.get("telegram", {})
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN") or tg_raw.get("bot_token")
    tg_chat = os.getenv("TELEGRAM_CHAT_ID") or tg_raw.get("chat_id")

    tg_cfg = TelegramConfig(
        enabled=bool(tg_raw.get("enabled", True)),
        send_heartbeat=bool(tg_raw.get("send_heartbeat", True)),
        heartbeat_interval_minutes=int(tg_raw.get("heartbeat_interval_minutes", 60)),
        daily_report_hour=str(tg_raw.get("daily_report_hour", "20:00")),
        bot_token=tg_token,
        chat_id=tg_chat
    )

    # 7. Database & Bot Loop
    db_raw = raw_cfg.get("database", {})
    db_cfg = DatabaseConfig(db_path=str(db_raw.get("db_path", "trading_bot.db")))

    loop_raw = raw_cfg.get("bot_loop", {})
    loop_cfg = BotLoopConfig(check_interval_seconds=int(loop_raw.get("check_interval_seconds", 60)))

    # Validaciones críticas
    _validate_config(mode_cfg, exc_cfg, risk_cfg, strat_cfg)

    return AppConfig(
        mode=mode_cfg,
        exchange=exc_cfg,
        symbols=symbols,
        strategy=strat_cfg,
        risk=risk_cfg,
        telegram=tg_cfg,
        database=db_cfg,
        bot_loop=loop_cfg
    )


def _validate_config(
    mode: ModeConfig,
    exchange: ExchangeConfig,
    risk: RiskConfig,
    strat: StrategyConfig
) -> None:
    """Valida límites lógicos de parámetros de trading y riesgo."""
    if not mode.dry_run:
        if not exchange.api_key or not exchange.api_secret:
            logger.warning(
                "¡ATENCIÓN! El bot está en modo REAL (dry_run: false) pero faltan EXCHANGE_API_KEY o EXCHANGE_API_SECRET."
            )

    if not (0.1 <= risk.position_size_percent <= 100.0):
        raise ValueError(f"position_size_percent ({risk.position_size_percent}) debe estar entre 0.1% y 100.0%")

    if risk.stop_loss_percent <= 0:
        raise ValueError(f"stop_loss_percent ({risk.stop_loss_percent}) debe ser mayor a 0")

    if risk.take_profit_percent <= 0:
        raise ValueError(f"take_profit_percent ({risk.take_profit_percent}) debe ser mayor a 0")

    if risk.max_concurrent_trades < 1:
        raise ValueError("max_concurrent_trades debe ser al menos 1")

    if risk.trailing_stop.callback_percent <= 0:
        raise ValueError("trailing_stop.callback_percent debe ser mayor a 0")

    if strat.macro.limit_candles < strat.macro.sma_period:
        raise ValueError("strategy.macro.limit_candles debe ser mayor o igual a sma_period")
