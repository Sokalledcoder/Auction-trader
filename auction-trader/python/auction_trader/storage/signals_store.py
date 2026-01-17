"""Signals storage using SQLite.

Stores generated trading signals for analysis and audit.
"""

import logging
import sqlite3
from pathlib import Path
from typing import Optional, List
import json

from ..config import DatabaseConfig
from ..models.types import Signal, SignalType, Action

logger = logging.getLogger(__name__)


class SignalsStore:
    """SQLite storage for trading signals."""

    def __init__(self, config: DatabaseConfig):
        self.config = config
        self.db_path = Path(config.data_dir) / config.signals_db
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> None:
        """Connect to the database."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._create_tables()
        logger.info(f"Connected to signals store: {self.db_path}")

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def _create_tables(self) -> None:
        """Create tables if they don't exist."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_min INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                signal_type TEXT,
                action TEXT NOT NULL,
                stop_price REAL,
                tp1_price REAL,
                tp2_price REAL,
                size REAL,
                strategy_tag TEXT,
                confidence REAL,
                reason TEXT,
                features_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_signals_ts
            ON signals (ts_min)
        """)

        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_signals_symbol
            ON signals (symbol)
        """)

        self._conn.commit()

    def insert_signal(self, signal: Signal, symbol: str) -> int:
        """Insert a signal and return its ID."""
        # Serialize features snapshot if present
        features_json = None
        if signal.features_snapshot:
            fs = signal.features_snapshot
            features_json = json.dumps({
                "ts_min": fs.ts_min,
                "mid_close": fs.mid_close,
                "sigma_240": fs.sigma_240,
                "va_poc": fs.va.poc,
                "va_vah": fs.va.vah,
                "va_val": fs.va.val,
                "of_1m": fs.order_flow.of_1m,
                "of_norm_1m": fs.order_flow.of_norm_1m,
                "qimb_ema": fs.qimb_ema,
            })

        cursor = self._conn.execute("""
            INSERT INTO signals (
                ts_min, symbol, signal_type, action, stop_price, tp1_price,
                tp2_price, size, strategy_tag, confidence, reason, features_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            signal.ts_min,
            symbol,
            signal.signal_type.name if signal.signal_type else None,
            signal.action.name,
            signal.stop_price,
            signal.tp1_price,
            signal.tp2_price,
            signal.size,
            signal.strategy_tag,
            signal.confidence,
            signal.reason,
            features_json,
        ])

        self._conn.commit()
        return cursor.lastrowid

    def get_signals(
        self,
        symbol: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        signal_type: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[dict]:
        """Query signals with optional filters."""
        query = "SELECT * FROM signals WHERE symbol = ?"
        params = [symbol]

        if start_ts:
            query += " AND ts_min >= ?"
            params.append(start_ts)

        if end_ts:
            query += " AND ts_min <= ?"
            params.append(end_ts)

        if signal_type:
            query += " AND signal_type = ?"
            params.append(signal_type)

        query += " ORDER BY ts_min DESC"

        if limit:
            query += f" LIMIT {limit}"

        cursor = self._conn.execute(query, params)
        rows = cursor.fetchall()

        return [dict(row) for row in rows]

    def get_signal_counts(
        self,
        symbol: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
    ) -> dict:
        """Get signal counts by type."""
        query = """
            SELECT signal_type, COUNT(*) as count
            FROM signals
            WHERE symbol = ?
        """
        params = [symbol]

        if start_ts:
            query += " AND ts_min >= ?"
            params.append(start_ts)

        if end_ts:
            query += " AND ts_min <= ?"
            params.append(end_ts)

        query += " GROUP BY signal_type"

        cursor = self._conn.execute(query, params)
        rows = cursor.fetchall()

        return {row["signal_type"] or "HOLD": row["count"] for row in rows}

    def get_latest_signal(self, symbol: str) -> Optional[dict]:
        """Get the most recent signal."""
        signals = self.get_signals(symbol, limit=1)
        return signals[0] if signals else None

    def delete_old_signals(self, before_ts: int, symbol: str) -> int:
        """Delete signals older than a timestamp."""
        cursor = self._conn.execute(
            "DELETE FROM signals WHERE ts_min < ? AND symbol = ?",
            [before_ts, symbol]
        )
        self._conn.commit()
        return cursor.rowcount
