"""
Módulo del Motor de Estrategia Técnica Multitemporal.
Implementa el análisis Macro en 1D (SMA 10 días + ADX 14) y Micro en 1H (RSI 14 + Volumen promedio).
"""
import logging
from dataclasses import dataclass
from typing import Optional
import pandas as pd
import numpy as np
import ta

from .config_loader import StrategyConfig

logger = logging.getLogger(__name__)


@dataclass
class SignalResult:
    symbol: str
    action: str  # "BUY", "HOLD", "NO_ACTION"
    macro_passed: bool
    micro_passed: bool
    current_price: float
    sma_10d: float
    adx_1d: float
    rsi_1h: float
    current_volume_1h: float
    avg_volume_1h: float
    reason: str


class MultiTimeframeStrategy:
    """
    Estrategia de trading multitemporal:
    1. Macro (1D): Evalúa la tendencia general de los últimos 10 días (Precio > SMA_10 y ADX > adx_min_strength).
    2. Micro (1H): Filtra la entrada con RSI y confirmación de volumen respecto a la media móvil.
    """

    def __init__(self, config: StrategyConfig):
        self.config = config

    def evaluate_macro_trend(self, df_1d: pd.DataFrame) -> tuple[bool, float, float, str]:
        """
        Evalúa el marco temporal diario (1D) analizando la tendencia y fuerza direccional de los últimos 10-30 días.
        Condiciones para Macro Alcista / Momentum Favorable:
        - Último precio de cierre > SMA(10 días) o rebote activo con pendiente positiva.
        - ADX(14) >= adx_min_strength (ej. 18.0) para asegurar que hay tendencia y momentum real.
        """
        if df_1d.empty or len(df_1d) < max(self.config.macro.sma_period, self.config.macro.adx_period + 2):
            return False, 0.0, 0.0, "Velas insuficientes para marco 1D"

        df = df_1d.copy()
        df["sma_10"] = ta.trend.sma_indicator(df["close"], window=self.config.macro.sma_period)

        adx_indicator = ta.trend.ADXIndicator(
            high=df["high"],
            low=df["low"],
            close=df["close"],
            window=self.config.macro.adx_period
        )
        df["adx"] = adx_indicator.adx()

        last_row = df.iloc[-1]
        prev_row = df.iloc[-2] if len(df) >= 2 else last_row

        close_price = float(last_row["close"])
        sma_val = float(last_row["sma_10"]) if not np.isnan(last_row["sma_10"]) else 0.0
        adx_val = float(last_row["adx"]) if not np.isnan(last_row["adx"]) else 0.0

        # Tendencia alcista clásica o cruce/recuperación sobre la media de 10 días
        is_above_sma = close_price >= sma_val
        is_strong_trend = adx_val >= self.config.macro.adx_min_strength

        if is_above_sma and is_strong_trend:
            return True, sma_val, adx_val, f"Macro Alcista (Precio {close_price:.2f} >= SMA10 {sma_val:.2f}, ADX {adx_val:.1f} >= {self.config.macro.adx_min_strength})"
        
        reasons = []
        if not is_above_sma:
            reasons.append(f"Precio ({close_price:.2f}) < SMA10 ({sma_val:.2f})")
        if not is_strong_trend:
            reasons.append(f"ADX ({adx_val:.1f} < {self.config.macro.adx_min_strength})")

        return False, sma_val, adx_val, " / ".join(reasons)

    def evaluate_micro_entry(self, df_1h: pd.DataFrame) -> tuple[bool, float, float, float, str]:
        """
        Evalúa el marco temporal horario (1H) para sincronizar compras con alta probabilidad de ganancia:
        - RSI(14) en rango óptimo de entrada (ej. entre 30 y 62) para evitar sobrecompra y caídas sin suelo.
        - Volumen actual > Promedio de volumen * volume_factor.
        """
        min_candles = max(self.config.micro.rsi_period, self.config.micro.volume_sma_period) + 2
        if df_1h.empty or len(df_1h) < min_candles:
            return False, 0.0, 0.0, 0.0, "Velas insuficientes para marco 1H"

        df = df_1h.copy()
        
        # RSI 14
        df["rsi"] = ta.momentum.rsi(df["close"], window=self.config.micro.rsi_period)

        # Promedio de volumen móvil
        df["vol_sma"] = df["volume"].rolling(window=self.config.micro.volume_sma_period).mean()

        last_row = df.iloc[-1]
        rsi_val = float(last_row["rsi"]) if not np.isnan(last_row["rsi"]) else 50.0
        current_vol = float(last_row["volume"])
        avg_vol = float(last_row["vol_sma"]) if not np.isnan(last_row["vol_sma"]) else 0.0

        required_vol = avg_vol * self.config.micro.volume_factor
        min_rsi = getattr(self.config.micro, "rsi_min_entry", 30.0)
        max_rsi = self.config.micro.rsi_max_entry

        rsi_condition = min_rsi <= rsi_val <= max_rsi
        volume_condition = current_vol >= required_vol if required_vol > 0 else True

        if rsi_condition and volume_condition:
            return True, rsi_val, current_vol, avg_vol, f"Micro Óptimo (RSI {rsi_val:.1f} en [{min_rsi}-{max_rsi}], Vol {current_vol:.1f} >= {required_vol:.1f})"

        reasons = []
        if not rsi_condition:
            if rsi_val > max_rsi:
                reasons.append(f"RSI sobrecomprado ({rsi_val:.1f} > {max_rsi})")
            else:
                reasons.append(f"RSI en sobreventa extrema sin rebote ({rsi_val:.1f} < {min_rsi})")
        if not volume_condition:
            reasons.append(f"Volumen ({current_vol:.1f} < Promedio {required_vol:.1f})")

        return False, rsi_val, current_vol, avg_vol, " / ".join(reasons)


    def analyze(self, symbol: str, df_1d: pd.DataFrame, df_1h: pd.DataFrame, current_price: Optional[float] = None) -> SignalResult:
        """
        Ejecuta el análisis completo multitemporal combinando 1D y 1H.
        Emite señal BUY únicamente si ambas temporalidades validan la entrada.
        """
        price = current_price if current_price is not None else (df_1h.iloc[-1]["close"] if not df_1h.empty else 0.0)

        macro_ok, sma_10, adx_val, macro_reason = self.evaluate_macro_trend(df_1d)
        micro_ok, rsi_val, cur_vol, avg_vol, micro_reason = self.evaluate_micro_entry(df_1h)

        if macro_ok and micro_ok:
            action = "BUY"
            reason = f"Señal de COMPRA confirmada. Macro: {macro_reason}. Micro: {micro_reason}"
        else:
            action = "HOLD"
            reasons = []
            if not macro_ok:
                reasons.append(f"[Macro 1D]: {macro_reason}")
            if not micro_ok:
                reasons.append(f"[Micro 1H]: {micro_reason}")
            reason = " | ".join(reasons)

        return SignalResult(
            symbol=symbol,
            action=action,
            macro_passed=macro_ok,
            micro_passed=micro_ok,
            current_price=price,
            sma_10d=sma_10,
            adx_1d=adx_val,
            rsi_1h=rsi_val,
            current_volume_1h=cur_vol,
            avg_volume_1h=avg_vol,
            reason=reason
        )
