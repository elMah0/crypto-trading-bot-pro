"""
Módulo de Evaluación de Operaciones impulsado por Inteligencia Artificial (Google Gemini AI).
Analiza las oportunidades técnicas y emite un score de confianza (0-100%), recomendación y niveles adaptativos.
"""
import os
import json
import logging
from dataclasses import dataclass
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class AIEvaluationResult:
    recommendation: str           # "CONFIRM_BUY", "REJECT_BUY", "HOLD"
    confidence_score: float       # 0.0 a 100.0
    reasoning: str                # Explicación narrativa en español
    suggested_tp_percent: float   # Porcentaje TP sugerido por IA
    suggested_sl_percent: float   # Porcentaje SL sugerido por IA
    is_fallback: bool = False     # True si se usó motor de respaldo sin API Key


class AIEvaluator:
    """
    Evaluador Inteligente basado en Google Gemini AI para filtrado de señales de trading.
    """

    def __init__(self, ai_config: Any, default_tp: float = 2.0, default_sl: float = 1.5):
        self.config = ai_config
        self.default_tp = default_tp
        self.default_sl = default_sl
        self.api_key = os.getenv("GEMINI_API_KEY") or getattr(ai_config, "api_key", None)
        self._model = None
        self._init_gemini()

    def _init_gemini(self) -> None:
        """Inicializa el cliente de Google Gemini si hay una API Key disponible."""
        if not self.api_key or self.api_key.startswith("tu_"):
            logger.info("AIEvaluator: No se detectó GEMINI_API_KEY. Usando motor de evaluación técnica Heurístico.")
            return

        try:
            try:
                import google.generativeai as genai
                genai.configure(api_key=self.api_key)
                model_name = getattr(self.config, "model", "gemini-flash-latest")
                self._model = genai.GenerativeModel(model_name)
                logger.info(f"AIEvaluator: Motor Google Gemini AI inicializado ({model_name}).")
            except ImportError:
                logger.warning("Librería google-generativeai no encontrada. Usando motor heurístico.")
        except Exception as e:
            logger.warning(f"Error inicializando Google Gemini AI: {e}. Usando motor heurístico.")

    def evaluate_signal(
        self,
        symbol: str,
        technical_signal: Dict[str, Any],
        current_price: float,
        macro_info: Dict[str, Any],
        micro_info: Dict[str, Any]
    ) -> AIEvaluationResult:
        """
        Evalúa una señal técnica de trading utilizando Gemini AI o el motor heurístico de respaldo.
        """
        if not getattr(self.config, "enabled", True):
            return AIEvaluationResult(
                recommendation="CONFIRM_BUY" if technical_signal.get("action") == "BUY" else "HOLD",
                confidence_score=80.0,
                reasoning="IA desactivada en configuración. Señal aprobada por reglas técnicas.",
                suggested_tp_percent=self.default_tp,
                suggested_sl_percent=self.default_sl,
                is_fallback=True
            )

        if self._model:
            try:
                return self._evaluate_with_gemini(symbol, technical_signal, current_price, macro_info, micro_info)
            except Exception as e:
                logger.warning(f"Excepción en consulta a Gemini AI ({e}). Aplicando evaluación heurística de respaldo.")

        return self._evaluate_heuristic(symbol, technical_signal, current_price, macro_info, micro_info)

    def _evaluate_with_gemini(
        self,
        symbol: str,
        technical_signal: Dict[str, Any],
        current_price: float,
        macro_info: Dict[str, Any],
        micro_info: Dict[str, Any]
    ) -> AIEvaluationResult:
        """Envía prompt estructurado a Gemini AI y procesa la respuesta JSON."""
        prompt = f"""
Eres un trader cuantitativo experto en criptomonedas.
Evalúa la siguiente oportunidad de COMPRA para el par {symbol}:

- Precio Actual: {current_price:.4f}
- Diagnóstico Técnico: {technical_signal.get('reason')}
- Filtro Macro (1D): SMA10={macro_info.get('sma_10d', 0):.4f}, ADX={macro_info.get('adx_1d', 0):.1f}
- Filtro Micro (1H): RSI={micro_info.get('rsi_1h', 0):.1f}, Vol Actual={micro_info.get('current_volume_1h', 0):.1f}, Vol Prom={micro_info.get('avg_volume_1h', 0):.1f}

Responde EXCLUSIVAMENTE en formato JSON con la siguiente estructura (sin formato Markdown adicional):
{{
    "recommendation": "CONFIRM_BUY" o "REJECT_BUY",
    "confidence_score": número entre 0 y 100,
    "reasoning": "Breve explicación en español de 1-2 oraciones indicando por qué se aprueba o rechaza la entrada",
    "suggested_tp_percent": número float de Take Profit recomendado (ej: 2.5),
    "suggested_sl_percent": número float de Stop Loss recomendado (ej: 1.5)
}}
"""
        response = self._model.generate_content(prompt)
        text = response.text.strip()

        # Limpiar posible marcado ```json ... ```
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        data = json.loads(text)
        return AIEvaluationResult(
            recommendation=str(data.get("recommendation", "CONFIRM_BUY")).upper(),
            confidence_score=float(data.get("confidence_score", 75.0)),
            reasoning=str(data.get("reasoning", "Evaluado por Gemini AI")),
            suggested_tp_percent=float(data.get("suggested_tp_percent", self.default_tp)),
            suggested_sl_percent=float(data.get("suggested_sl_percent", self.default_sl)),
            is_fallback=False
        )

    def _evaluate_heuristic(
        self,
        symbol: str,
        technical_signal: Dict[str, Any],
        current_price: float,
        macro_info: Dict[str, Any],
        micro_info: Dict[str, Any]
    ) -> AIEvaluationResult:
        """Motor heurístico de respaldo que calcula un score técnico determinista."""
        action = technical_signal.get("action", "WAIT")
        if action != "BUY":
            return AIEvaluationResult(
                recommendation="HOLD",
                confidence_score=40.0,
                reasoning="Sin señal de compra confirmada en indicadores.",
                suggested_tp_percent=self.default_tp,
                suggested_sl_percent=self.default_sl,
                is_fallback=True
            )

        score = 70.0
        reasons = []

        adx = macro_info.get("adx_1d", 0)
        if adx >= 25.0:
            score += 15.0
            reasons.append("Tendencia macro muy fuerte (ADX > 25)")
        elif adx >= 18.0:
            score += 5.0

        rsi = micro_info.get("rsi_1h", 50)
        if 35.0 <= rsi <= 55.0:
            score += 10.0
            reasons.append("RSI en zona óptima de impulso (35-55)")

        vol_cur = micro_info.get("current_volume_1h", 0)
        vol_avg = micro_info.get("avg_volume_1h", 1)
        if vol_avg > 0 and (vol_cur / vol_avg) >= 1.2:
            score += 5.0
            reasons.append("Volumen superior al promedio (+20%)")

        score = min(score, 98.0)
        recommendation = "CONFIRM_BUY" if score >= getattr(self.config, "min_confidence_score", 70.0) else "REJECT_BUY"
        reasoning_text = " | ".join(reasons) if reasons else "Filtros técnicos dentro de parámetros normales."

        return AIEvaluationResult(
            recommendation=recommendation,
            confidence_score=round(score, 1),
            reasoning=f"[Motor Heurístico] {reasoning_text}",
            suggested_tp_percent=self.default_tp,
            suggested_sl_percent=self.default_sl,
            is_fallback=True
        )
