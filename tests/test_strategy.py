import pytest
import pandas as pd
import numpy as np
from src.config_loader import StrategyConfig
from src.strategy import MultiTimeframeStrategy


def create_synthetic_candles(n: int, start_price: float, trend_slope: float, volume_base: float = 100.0) -> pd.DataFrame:
    """Crea velas sintéticas con una tendencia controlada."""
    timestamps = pd.date_range("2026-01-01", periods=n, freq="1h")
    prices = [start_price + i * trend_slope for i in range(n)]
    highs = [p * 1.01 for p in prices]
    lows = [p * 0.99 for p in prices]
    volumes = [volume_base + (i % 5) * 10 for i in range(n)]
    
    df = pd.DataFrame({
        "timestamp": timestamps,
        "open": prices,
        "high": highs,
        "low": lows,
        "close": prices,
        "volume": volumes
    })
    df.set_index("timestamp", inplace=True)
    return df


def test_strategy_macro_bullish():
    strat_cfg = StrategyConfig()
    strat = MultiTimeframeStrategy(strat_cfg)
    
    # Serie de 30 días alcista fuerte
    df_1d = create_synthetic_candles(30, start_price=50000.0, trend_slope=500.0)
    passed, sma_val, adx_val, reason = strat.evaluate_macro_trend(df_1d)
    
    assert sma_val > 0
    # Al ser tendencia lineal ascendente constante, precio > sma y adx alto
    assert passed is True
    assert "Macro Alcista" in reason


def test_strategy_micro_entry():
    strat_cfg = StrategyConfig()
    strat = MultiTimeframeStrategy(strat_cfg)
    
    # Serie 1H oscilante con leve retroceso para mantener RSI bajo y volumen alto al final
    np.random.seed(42)
    n = 50
    timestamps = pd.date_range("2026-01-01", periods=n, freq="1h")
    # Generar precios oscilantes en rango (sin subida lineal continua que infle el RSI a 100)
    prices = 100.0 + np.sin(np.linspace(0, 3 * np.pi, n)) * 2.0
    highs = prices + 0.5
    lows = prices - 0.5
    volumes = [100.0] * n
    volumes[-1] = 500.0  # Gran volumen en la última vela
    
    df_1h = pd.DataFrame({
        "timestamp": timestamps,
        "open": prices,
        "high": highs,
        "low": lows,
        "close": prices,
        "volume": volumes
    })
    df_1h.set_index("timestamp", inplace=True)
    
    passed, rsi_val, cur_vol, avg_vol, reason = strat.evaluate_micro_entry(df_1h)
    assert passed is True, f"Fallo micro con reason: {reason}, rsi: {rsi_val}"
    assert cur_vol >= avg_vol
    assert rsi_val <= strat_cfg.micro.rsi_max_entry

