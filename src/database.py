"""
Módulo de Gestión de Base de Datos SQLite para registro de órdenes, historial de operaciones y métricas.
"""
import sqlite3
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

from .config_loader import DatabaseConfig

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    id: Optional[int]
    symbol: str
    order_type: str  # "BUY" o "SELL"
    entry_price: float
    exit_price: Optional[float]
    amount: float
    cost: float
    pnl_amount: Optional[float]
    pnl_percent: Optional[float]
    fee: float
    status: str  # "OPEN", "CLOSED", "CANCELLED"
    exit_reason: Optional[str]  # "TAKE_PROFIT", "STOP_LOSS", "TRAILING_STOP", "MANUAL", "CIRCUIT_BREAKER"
    opened_at: str
    closed_at: Optional[str]
    is_dry_run: bool


class DatabaseManager:
    """
    Gestiona la persistencia de operaciones comerciales y balances en SQLite.
    """

    def __init__(self, db_config: DatabaseConfig):
        self.db_path = db_config.db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Crea las tablas necesarias si no existen."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Tabla de operaciones (trades)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    order_type TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL,
                    amount REAL NOT NULL,
                    cost REAL NOT NULL,
                    pnl_amount REAL,
                    pnl_percent REAL,
                    fee REAL DEFAULT 0.0,
                    status TEXT NOT NULL,
                    exit_reason TEXT,
                    opened_at TEXT NOT NULL,
                    closed_at TEXT,
                    is_dry_run INTEGER NOT NULL
                )
            """)            # Migración de columnas de parámetros individuales por posición
            cursor.execute("PRAGMA table_info(trades)")
            cols = [info[1] for info in cursor.fetchall()]
            if "stop_loss_percent" not in cols:
                cursor.execute("ALTER TABLE trades ADD COLUMN stop_loss_percent REAL DEFAULT 1.5")
            if "take_profit_percent" not in cols:
                cursor.execute("ALTER TABLE trades ADD COLUMN take_profit_percent REAL DEFAULT 2.0")
            if "trailing_stop_enabled" not in cols:
                cursor.execute("ALTER TABLE trades ADD COLUMN trailing_stop_enabled INTEGER DEFAULT 1")
            if "trailing_activation_percent" not in cols:
                cursor.execute("ALTER TABLE trades ADD COLUMN trailing_activation_percent REAL DEFAULT 1.2")
            if "trailing_callback_percent" not in cols:
                cursor.execute("ALTER TABLE trades ADD COLUMN trailing_callback_percent REAL DEFAULT 0.8")
            if "enable_breakeven" not in cols:
                cursor.execute("ALTER TABLE trades ADD COLUMN enable_breakeven INTEGER DEFAULT 1")

            # Tabla de snapshots diarios / rendimiento
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    total_balance REAL NOT NULL,
                    free_balance REAL NOT NULL,
                    open_trades_count INTEGER NOT NULL,
                    closed_trades_today INTEGER NOT NULL,
                    daily_pnl_amount REAL NOT NULL,
                    daily_pnl_percent REAL NOT NULL,
                    is_dry_run INTEGER NOT NULL
                )
            """)

            # Índices para consultas rápidas
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_opened_at ON trades(opened_at)")
            conn.commit()
            logger.info(f"Base de datos SQLite inicializada en '{self.db_path}'")

    def insert_trade_open(
        self,
        symbol: str,
        entry_price: float,
        amount: float,
        cost: float,
        fee: float,
        is_dry_run: bool,
        stop_loss_percent: float = 1.5,
        take_profit_percent: float = 2.0,
        trailing_stop_enabled: bool = True,
        trailing_activation_percent: float = 1.2,
        trailing_callback_percent: float = 0.8,
        enable_breakeven: bool = True
    ) -> int:
        """Registra la apertura de una nueva posición con sus parámetros individuales."""
        now_utc = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO trades (
                    symbol, order_type, entry_price, amount, cost, fee,
                    status, opened_at, is_dry_run,
                    stop_loss_percent, take_profit_percent, trailing_stop_enabled,
                    trailing_activation_percent, trailing_callback_percent, enable_breakeven
                ) VALUES (?, 'BUY', ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                symbol, entry_price, amount, cost, fee, now_utc, 1 if is_dry_run else 0,
                stop_loss_percent, take_profit_percent, 1 if trailing_stop_enabled else 0,
                trailing_activation_percent, trailing_callback_percent, 1 if enable_breakeven else 0
            ))
            conn.commit()
            return cursor.lastrowid

    def update_trade_parameters(
        self,
        trade_id: int,
        stop_loss_percent: Optional[float] = None,
        take_profit_percent: Optional[float] = None,
        trailing_stop_enabled: Optional[bool] = None,
        trailing_activation_percent: Optional[float] = None,
        trailing_callback_percent: Optional[float] = None,
        enable_breakeven: Optional[bool] = None
    ) -> None:
        """Actualiza los parámetros individuales de una posición abierta en SQLite."""
        updates = []
        params = []
        if stop_loss_percent is not None:
            updates.append("stop_loss_percent = ?")
            params.append(stop_loss_percent)
        if take_profit_percent is not None:
            updates.append("take_profit_percent = ?")
            params.append(take_profit_percent)
        if trailing_stop_enabled is not None:
            updates.append("trailing_stop_enabled = ?")
            params.append(1 if trailing_stop_enabled else 0)
        if trailing_activation_percent is not None:
            updates.append("trailing_activation_percent = ?")
            params.append(trailing_activation_percent)
        if trailing_callback_percent is not None:
            updates.append("trailing_callback_percent = ?")
            params.append(trailing_callback_percent)
        if enable_breakeven is not None:
            updates.append("enable_breakeven = ?")
            params.append(1 if enable_breakeven else 0)

        if not updates:
            return

        params.append(trade_id)
        query = f"UPDATE trades SET {', '.join(updates)} WHERE id = ?"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()

    def close_trade(
        self,
        trade_id: int,
        exit_price: float,
        pnl_amount: float,
        pnl_percent: float,
        exit_reason: str,
        fee: float = 0.0
    ) -> None:
        """Actualiza una posición a cerrada y registra el resultado neto."""
        now_utc = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE trades
                SET exit_price = ?,
                    pnl_amount = ?,
                    pnl_percent = ?,
                    fee = fee + ?,
                    status = 'CLOSED',
                    exit_reason = ?,
                    closed_at = ?
                WHERE id = ?
            """, (exit_price, pnl_amount, pnl_percent, fee, exit_reason, now_utc, trade_id))
            conn.commit()

    def get_open_trades(self, is_dry_run: Optional[bool] = None) -> List[Dict[str, Any]]:
        """Retorna todas las posiciones abiertas activas."""
        query = "SELECT * FROM trades WHERE status = 'OPEN'"
        params = []
        if is_dry_run is not None:
            query += " AND is_dry_run = ?"
            params.append(1 if is_dry_run else 0)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def get_last_closed_trade_for_symbol(self, symbol: str, is_dry_run: bool) -> Optional[Dict[str, Any]]:
        """Obtiene la última operación cerrada para un símbolo específico (usado para cooldown)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM trades 
                WHERE symbol = ? AND is_dry_run = ? AND status = 'CLOSED'
                ORDER BY id DESC LIMIT 1
            """, (symbol, 1 if is_dry_run else 0))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_pnl_last_24h(self, is_dry_run: bool) -> float:
        """
        Calcula la sumatoria del PnL monetario de operaciones cerradas en las últimas 24 horas.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT SUM(pnl_amount) as total_pnl
                FROM trades
                WHERE is_dry_run = ? 
                  AND status = 'CLOSED' 
                  AND closed_at >= datetime('now', '-1 day')
            """, (1 if is_dry_run else 0,))
            row = cursor.fetchone()
            if row and row["total_pnl"] is not None:
                return float(row["total_pnl"])
            return 0.0

    def get_trades_summary_today(self, is_dry_run: bool) -> Dict[str, Any]:
        """Obtiene el resumen de operaciones cerradas en el día de hoy (UTC)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 
                    COUNT(*) as count,
                    SUM(CASE WHEN pnl_amount > 0 THEN 1 ELSE 0 END) as win_count,
                    SUM(CASE WHEN pnl_amount < 0 THEN 1 ELSE 0 END) as loss_count,
                    SUM(pnl_amount) as total_pnl,
                    SUM(fee) as total_fees
                FROM trades
                WHERE is_dry_run = ? 
                  AND status = 'CLOSED' 
                  AND date(closed_at) = date('now')
            """, (1 if is_dry_run else 0,))
            row = cursor.fetchone()
            if not row or row["count"] == 0:
                return {
                    "count": 0,
                    "win_count": 0,
                    "loss_count": 0,
                    "win_rate": 0.0,
                    "total_pnl": 0.0,
                    "total_fees": 0.0
                }
            
            count = int(row["count"])
            win_count = int(row["win_count"] or 0)
            total_pnl = float(row["total_pnl"] or 0.0)
            win_rate = (win_count / count) * 100.0 if count > 0 else 0.0

            return {
                "count": count,
                "win_count": win_count,
                "loss_count": int(row["loss_count"] or 0),
                "win_rate": win_rate,
                "total_pnl": total_pnl,
                "total_fees": float(row["total_fees"] or 0.0)
            }

    def record_daily_snapshot(
        self,
        total_balance: float,
        free_balance: float,
        open_trades_count: int,
        closed_trades_today: int,
        daily_pnl_amount: float,
        daily_pnl_percent: float,
        is_dry_run: bool
    ) -> None:
        """Guarda un registro histórico diario para seguimiento de balance."""
        now_utc = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO daily_snapshots (
                    timestamp, total_balance, free_balance, open_trades_count,
                    closed_trades_today, daily_pnl_amount, daily_pnl_percent, is_dry_run
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                now_utc, total_balance, free_balance, open_trades_count,
                closed_trades_today, daily_pnl_amount, daily_pnl_percent,
                1 if is_dry_run else 0
            ))
            conn.commit()

    def get_all_time_stats(self, is_dry_run: bool) -> Dict[str, Any]:
        """Obtiene métricas acumuladas históricas (total PnL, ganados, perdidos y comisiones)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 
                    COUNT(*) as count,
                    SUM(CASE WHEN pnl_amount > 0 THEN 1 ELSE 0 END) as win_count,
                    SUM(CASE WHEN pnl_amount < 0 THEN 1 ELSE 0 END) as loss_count,
                    SUM(pnl_amount) as total_pnl,
                    SUM(fee) as total_fees
                FROM trades
                WHERE is_dry_run = ? AND status = 'CLOSED'
            """, (1 if is_dry_run else 0,))
            row = cursor.fetchone()
            if not row or row["count"] == 0:
                return {
                    "count": 0,
                    "win_count": 0,
                    "loss_count": 0,
                    "win_rate": 0.0,
                    "total_pnl": 0.0,
                    "total_fees": 0.0
                }
            count = int(row["count"])
            win_count = int(row["win_count"] or 0)
            return {
                "count": count,
                "win_count": win_count,
                "loss_count": int(row["loss_count"] or 0),
                "win_rate": (win_count / count) * 100.0 if count > 0 else 0.0,
                "total_pnl": float(row["total_pnl"] or 0.0),
                "total_fees": float(row["total_fees"] or 0.0)
            }

