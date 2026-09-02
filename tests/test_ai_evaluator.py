import pytest
from src.ai_evaluator import AIEvaluator, AIEvaluationResult
from src.config_loader import AIConfig


def test_ai_evaluator_heuristic_fallback():
    cfg = AIConfig(enabled=True, min_confidence_score=70.0, provider="gemini")
    evaluator = AIEvaluator(cfg, default_tp=3.0, default_sl=1.5)

    # Test BUY signal with strong technical indicators
    tech_signal = {"action": "BUY", "reason": "Test Signal"}
    macro_info = {"sma_10d": 50000.0, "adx_1d": 28.0}
    micro_info = {"rsi_1h": 45.0, "current_volume_1h": 150.0, "avg_volume_1h": 100.0}

    result = evaluator.evaluate_signal("BTC/USDT", tech_signal, 50500.0, macro_info, micro_info)

    assert isinstance(result, AIEvaluationResult)
    assert result.recommendation == "CONFIRM_BUY"
    assert result.confidence_score >= 70.0
    assert result.suggested_tp_percent == 3.0
    assert result.suggested_sl_percent == 1.5
    assert result.is_fallback is True


def test_ai_evaluator_disabled():
    cfg = AIConfig(enabled=False, min_confidence_score=70.0)
    evaluator = AIEvaluator(cfg)

    tech_signal = {"action": "BUY", "reason": "Test Signal"}
    result = evaluator.evaluate_signal("ETH/USDT", tech_signal, 2500.0, {}, {})

    assert result.recommendation == "CONFIRM_BUY"
    assert result.confidence_score == 80.0
