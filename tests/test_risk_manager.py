import pytest
import os
import tempfile
from src.config_loader import RiskConfig, ModeConfig, DatabaseConfig
from src.risk_manager import RiskManager, PositionState
from src.database import DatabaseManager


@pytest.fixture
def temp_db():
    import uuid
    # Usar base de datos SQLite en memoria única por test para evitar bloqueos de archivo en Windows
    db_file = f"file:memdb_{uuid.uuid4().hex}?mode=memory&cache=shared"
    db = DatabaseManager(DatabaseConfig(db_path=db_file))
    yield db



def test_position_sizing_and_initial_levels(temp_db):
    risk_cfg = RiskConfig(
        position_size_percent=10.0,
        stop_loss_percent=2.0,
        take_profit_percent=4.0
    )
    mode_cfg = ModeConfig(dry_run=True)
    rm = RiskManager(risk_cfg, mode_cfg, temp_db)

    # Balance libre: 1000 USDT, precio BTC: 50,000 USDT
    cost, amount = rm.calculate_position_size(free_balance=1000.0, current_price=50000.0)
    assert cost == 100.0  # 10% de 1000
    assert amount == 100.0 / 50000.0

    sl_price, tp_price = rm.calculate_initial_levels(entry_price=50000.0)
    assert sl_price == 50000.0 * 0.98  # 49000.0
    assert tp_price == 50000.0 * 1.04  # 52000.0


def test_trailing_stop_activation_and_callback(temp_db):
    risk_cfg = RiskConfig()
    risk_cfg.trailing_stop.activation_profit_percent = 2.0  # Activa al +2%
    risk_cfg.trailing_stop.callback_percent = 1.0           # Retroceso 1%
    mode_cfg = ModeConfig(dry_run=True)
    rm = RiskManager(risk_cfg, mode_cfg, temp_db)

    pos = PositionState(
        trade_id=1,
        symbol="ETH/USDT",
        entry_price=1000.0,
        amount=1.0,
        cost=1000.0,
        highest_price=1000.0,
        trailing_active=False,
        current_sl_price=980.0,
        tp_price=1050.0,
        opened_at="2026-01-01T00:00:00"
    )

    # 1. Precio sube a 1025 (+2.5%): Debe activar el Trailing Stop y colocar nuevo SL en 1025 * 0.99 = 1014.75
    exit_reason, pnl_amount, pnl_pct = rm.evaluate_position_exit(pos, current_price=1025.0)
    assert exit_reason is None
    assert pos.trailing_active is True
    assert pos.highest_price == 1025.0
    assert pos.current_sl_price == pytest.approx(1025.0 * 0.99)

    # 2. Precio sube a 1050: Alcanza Take Profit
    exit_reason, pnl_amount, pnl_pct = rm.evaluate_position_exit(pos, current_price=1050.0)
    assert exit_reason == "TAKE_PROFIT"
    assert pnl_pct == pytest.approx(5.0)
