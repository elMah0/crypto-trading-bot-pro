"""
Módulo del Cliente de Exchange utilizando CCXT con soporte para REST, OHLCV, tickers y balance.
"""
import time
import logging
from typing import Dict, Any, Optional, List
import pandas as pd
import ccxt

from .config_loader import ExchangeConfig, ModeConfig

logger = logging.getLogger(__name__)


class ExchangeClient:
    """
    Encapsula la interacción con CCXT para operaciones de mercado,
    obtención de velas OHLCV, consulta de precios y balance de cuenta.
    """

    def __init__(self, exchange_config: ExchangeConfig, mode_config: ModeConfig):
        self.config = exchange_config
        self.mode = mode_config
        self.exchange_name = exchange_config.name.lower()
        self.client: ccxt.Exchange = self._initialize_client()
        self._simulated_balance: float = mode_config.initial_simulated_balance

    def _initialize_client(self) -> ccxt.Exchange:
        """Inicializa la instancia de CCXT con la configuración establecida."""
        exchange_class = getattr(ccxt, self.exchange_name, None)
        if not exchange_class:
            raise ValueError(f"El exchange '{self.exchange_name}' no es soportado por CCXT.")

        options: Dict[str, Any] = {
            "enableRateLimit": True,
            "timeout": self.config.timeout_seconds * 1000,
        }

        # Adjuntar credenciales si existen en .env (tanto en simulación como en real)
        is_placeholder_secret = not self.config.api_secret or "tu_api_secret" in str(self.config.api_secret).lower() or len(str(self.config.api_secret)) < 10
        if self.config.api_key and not is_placeholder_secret:
            options["apiKey"] = self.config.api_key
            options["secret"] = self.config.api_secret
            options["options"] = {"adjustForTimeDifference": True, "recvWindow": 10000, "fetchCurrencies": False}
            if self.config.password:
                options["password"] = self.config.password

        client: ccxt.Exchange = exchange_class(options)

        if hasattr(client, "load_time_difference") and self.config.api_key and not is_placeholder_secret:
            try:
                client.load_time_difference()
            except Exception as e:
                logger.warning(f"No se pudo sincronizar diferencia de tiempo del servidor: {e}")

        if self.config.testnet:
            if hasattr(client, "set_sandbox_mode"):
                client.set_sandbox_mode(True)
                logger.info(f"Sandbox / Testnet activado para {self.exchange_name}")
            else:
                logger.warning(f"El exchange {self.exchange_name} no soporta set_sandbox_mode directamente en CCXT.")

        return client

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 50) -> pd.DataFrame:
        """
        Descarga velas OHLCV históricas para un símbolo y timeframe dado.
        Retorna un DataFrame de Pandas con columnas: ['timestamp', 'open', 'high', 'low', 'close', 'volume'].
        """
        retries = self.config.max_retries
        for attempt in range(1, retries + 1):
            try:
                # CCXT fetch_ohlcv devuelve [timestamp, open, high, low, close, volume]
                raw_ohlcv = self.client.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
                if not raw_ohlcv:
                    logger.warning(f"Respuesta OHLCV vacía para {symbol} ({timeframe})")
                    return pd.DataFrame()

                df = pd.DataFrame(raw_ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
                df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
                df.set_index("timestamp", inplace=True)
                df = df.astype(float)
                return df

            except (ccxt.NetworkError, ccxt.RateLimitExceeded, ccxt.ExchangeNotAvailable) as e:
                logger.warning(f"Intento {attempt}/{retries} falló para fetch_ohlcv({symbol}, {timeframe}): {e}")
                if attempt == retries:
                    logger.error(f"Error definitivo al obtener OHLCV para {symbol}: {e}")
                    raise
                time.sleep((self.config.rate_limit_delay_ms / 1000.0) * attempt * 2)
            except Exception as e:
                logger.error(f"Excepción inesperada en fetch_ohlcv({symbol}, {timeframe}): {e}")
                raise

        return pd.DataFrame()

    def fetch_ticker_price(self, symbol: str) -> float:
        """Obtiene el último precio de mercado en tiempo real para un símbolo."""
        retries = self.config.max_retries
        for attempt in range(1, retries + 1):
            try:
                ticker = self.client.fetch_ticker(symbol)
                price = ticker.get("last") or ticker.get("close")
                if price is None:
                    bid = ticker.get("bid")
                    ask = ticker.get("ask")
                    if bid and ask:
                        price = (bid + ask) / 2.0
                    else:
                        raise ValueError(f"No se pudo determinar el precio del ticker para {symbol}")
                return float(price)
            except (ccxt.NetworkError, ccxt.RateLimitExceeded) as e:
                logger.warning(f"Intento {attempt}/{retries} falló para fetch_ticker_price({symbol}): {e}")
                if attempt == retries:
                    raise
                time.sleep(1.0 * attempt)
            except Exception as e:
                logger.error(f"Error obteniendo ticker de {symbol}: {e}")
                raise
        return 0.0

    def get_free_balance(self, currency: Optional[str] = None) -> float:
        """
        Retorna el balance libre disponible.
        Lee la información real desde Binance si hay credenciales configuradas en .env.
        En modo simulación (dry_run), utiliza la información real como base para simular operaciones sin riesgo.
        """
        target_currency = currency or self.mode.quote_currency
        is_placeholder_secret = not self.config.api_secret or "tu_api_secret" in str(self.config.api_secret).lower() or len(str(self.config.api_secret)) < 10

        if self.config.api_key and not is_placeholder_secret:
            try:
                balance_data = self.client.fetch_balance()
                free_balance = balance_data.get("free", {}).get(target_currency, 0.0)
                if self.mode.dry_run and not getattr(self, "_real_balance_initialized", False):
                    if free_balance and float(free_balance) > 0:
                        self._simulated_balance = float(free_balance)
                    self._real_balance_initialized = True

                if not self.mode.dry_run:
                    return float(free_balance)
                return float(self._simulated_balance)
            except Exception as e:
                logger.warning(f"No se pudo consultar balance en tiempo real de Binance ({e}). Usando saldo simulado.")
                return float(self._simulated_balance)

        return float(self._simulated_balance)

    def update_simulated_balance(self, delta_amount: float) -> float:
        """Actualiza el balance virtual para el modo simulación."""
        if self.mode.dry_run:
            self._simulated_balance += delta_amount
            logger.debug(f"Balance simulado actualizado: {self._simulated_balance:.2f} {self.mode.quote_currency}")
        return self._simulated_balance

    def set_simulated_balance(self, amount: float) -> None:
        """Establece explícitamente el balance virtual."""
        self._simulated_balance = float(amount)

    def reconnect_exchange(self, exchange_config: ExchangeConfig) -> None:
        """Re-inicializa la instancia de CCXT al cambiar la configuración en caliente."""
        self.config = exchange_config
        self.exchange_name = exchange_config.name.lower()
        self.client = self._initialize_client()
        logger.info(f"Conexión con el exchange '{self.exchange_name.upper()}' re-inicializada exitosamente.")

    def fetch_top_gainers(self, top_n: int = 10) -> List[Dict[str, Any]]:
        """
        Consulta todos los tickers del mercado Spot USDT en Binance y devuelve las monedas con mayor porcentaje de alza en 24h.
        """
        try:
            tickers = self.client.fetch_tickers()
            usdt_tickers = []
            for symbol, t in tickers.items():
                if symbol.endswith("/USDT") and t.get("percentage") is not None:
                    usdt_tickers.append({
                        "symbol": symbol,
                        "price": float(t.get("last") or t.get("close") or 0.0),
                        "change_24h": float(t.get("percentage") or 0.0),
                        "volume_24h": float(t.get("quoteVolume") or t.get("baseVolume") or 0.0)
                    })
            usdt_tickers.sort(key=lambda x: x["change_24h"], reverse=True)
            return usdt_tickers[:top_n]
        except Exception as e:
            logger.error(f"Error consultando top gainers en Binance: {e}")
            return []
