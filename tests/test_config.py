import pytest
import os
import tempfile
import yaml
from src.config_loader import load_config, AppConfig


def test_load_default_config():
    cfg = load_config("config.yaml")
    assert isinstance(cfg, AppConfig)
    assert cfg.mode.dry_run is True
    assert cfg.mode.initial_simulated_balance == 200.0
    assert "BTC/USDT" in cfg.symbols
    assert cfg.risk.position_size_percent == 30.0
    assert cfg.strategy.macro.sma_period == 10



def test_invalid_position_size_percent():
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".yaml") as tmp:
        data = {
            "mode": {"dry_run": True},
            "symbols": ["BTC/USDT"],
            "risk": {"position_size_percent": 150.0}  # Inválido > 100%
        }
        yaml.dump(data, tmp)
        tmp_name = tmp.name

    try:
        with pytest.raises(ValueError, match="position_size_percent"):
            load_config(tmp_name)
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
