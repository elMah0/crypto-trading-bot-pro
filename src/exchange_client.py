"""
Módulo del Cliente de Exchange utilizando CCXT con soporte para REST, OHLCV, tickers y balance.
"""
import time
import logging
from typing import Dict, Any, Optional
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

        # Solo adjuntar credenciales privadas si NO estamos en simulación (dry_run) 
        # y la secret key no es una plantilla por defecto
        is_placeholder_secret = not self.config.api_secret or "tu_api_secret" in str(self.config.api_secret).lower() or len(str(self.config.api_secret)) < 10
        if not self.mode.dry_run and self.config.api_key and not is_placeholder_secret:
            options["apiKey"] = self.config.api_key
            options["secret"] = self.config.api_secret
            options["options"] = {"adjustForTimeDifference": True, "recvWindow": 10000, "fetchCurrencies": False}
            if self.config.password:
                options["password"] = self.config.password

        client: ccxt.Exchange = exchange_class(options)

        if not self.mode.dry_run and hasattr(client, "load_time_difference"):
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
        En modo simulación (dry_run), gestiona el balance virtual.
        En modo real, consulta la API del exchange.
        """
        target_currency = currency or self.mode.quote_currency
        if self.mode.dry_run:
            return float(self._simulated_balance)

        try:
            balance_data = self.client.fetch_balance()
            free_balance = balance_data.get("free", {}).get(target_currency, 0.0)
            return float(free_balance)
        except ccxt.AuthenticationError:
            msg = (
                f"No se pudieron consultar credenciales API para '{self.exchange_name.upper()}'. "
                "Para operar en MODO REAL debes configurar EXCHANGE_API_KEY y EXCHANGE_API_SECRET en tu archivo .env "
                "o cambiar a MODO SIMULACIÓN (dry_run: true) en la configuración."
            )
            logger.error(msg)
            raise ValueError(msg)
        except Exception as e:
            logger.error(f"Error consultando balance real en el exchange: {e}")
            raise

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
        """Re-inicializa la conexión CCXT con un nuevo exchange o configuración."""
        self.config = exchange_config
        self.exchange_name = exchange_config.name.lower()
        self.client = self._initialize_client()
        logger.info(f"Cliente de Exchange re-inicializado exitosamente para: {self.exchange_name.upper()}")

