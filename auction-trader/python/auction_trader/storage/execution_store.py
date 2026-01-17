"""Execution storage using SQLite.

Stores:
- Position state
- Trade history
- Order tracking
- Daily P&L
"""

import logging
import sqlite3
from pathlib import Path
from typing import Optional, List
from datetime import datetime

from ..config import DatabaseConfig
from ..models.types import Position, PositionSide
from ..services.position_manager import TradeRecord, ExitReason

logger = logging.getLogger(__name__)


class ExecutionStore:
    """SQLite storage for execution state and history."""

    def __init__(self, config: DatabaseConfig):
        self.config = config
        self.db_path = Path(config.data_dir) / config.execution_db
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> None:
        """Connect to the database."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._create_tables()
        logger.info(f"Connected to execution store: {self.db_path}")

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def _create_tables(self) -> None:
        """Create tables if they don't exist."""
        # Active position (singleton per symbol)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                symbol TEXT PRIMARY KEY,
                entry_ts INTEGER NOT NULL,
                side TEXT NOT NULL,
                entry_price REAL NOT NULL,
                size REAL NOT NULL,
                original_size REAL NOT NULL,
                stop_price REAL NOT NULL,
                tp1_price REAL,
                tp2_price REAL,
                tp1_hit INTEGER DEFAULT 0,
                strategy_tag TEXT,
                fees_paid REAL DEFAULT 0,
                funding_paid REAL DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Trade history
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                entry_ts INTEGER NOT NULL,
                exit_ts INTEGER NOT NULL,
                side TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                size REAL NOT NULL,
                pnl_gross REAL NOT NULL,
                pnl_net REAL NOT NULL,
                fees REAL NOT NULL,
                funding REAL NOT NULL,
                exit_reason TEXT NOT NULL,
                strategy_tag TEXT,
                hold_minutes INTEGER NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Daily P&L tracking
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_pnl (
                date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                realized_pnl REAL DEFAULT 0,
                unrealized_pnl REAL DEFAULT 0,
                trades_count INTEGER DEFAULT 0,
                win_count INTEGER DEFAULT 0,
                loss_count INTEGER DEFAULT 0,
                fees_total REAL DEFAULT 0,
                funding_total REAL DEFAULT 0,
                PRIMARY KEY (date, symbol)
            )
        """)

        # Order tracking
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL UNIQUE,
                client_order_id TEXT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT NOT NULL,
                price REAL,
                qty REAL NOT NULL,
                status TEXT NOT NULL,
                filled_qty REAL DEFAULT 0,
                avg_price REAL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_trades_symbol
            ON trades (symbol)
        """)

        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_trades_ts
            ON trades (exit_ts)
        """)

        self._conn.commit()

    # -------------------------------------------------------------------------
    # Position Management
    # -------------------------------------------------------------------------

    def save_position(self, position: Position, symbol: str) -> None:
        """Save or update the active position."""
        self._conn.execute("""
            INSERT OR REPLACE INTO positions (
                symbol, entry_ts, side, entry_price, size, original_size,
                stop_price, tp1_price, tp2_price, tp1_hit, strategy_tag,
                fees_paid, funding_paid, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            symbol,
            position.entry_ts,
            position.side.name,
            position.entry_price,
            position.size,
            position.original_size,
            position.stop_price,
            position.tp1_price,
            position.tp2_price,
            1 if position.tp1_hit else 0,
            position.strategy_tag,
            position.fees_paid,
            position.funding_paid,
            datetime.utcnow().isoformat(),
        ])
        self._conn.commit()

    def get_position(self, symbol: str) -> Optional[Position]:
        """Get the active position for a symbol."""
        cursor = self._conn.execute(
            "SELECT * FROM positions WHERE symbol = ?",
            [symbol]
        )
        row = cursor.fetchone()

        if not row:
            return None

        return Position(
            entry_ts=row["entry_ts"],
            side=PositionSide[row["side"]],
            entry_price=row["entry_price"],
            size=row["size"],
            original_size=row["original_size"],
            stop_price=row["stop_price"],
            tp1_price=row["tp1_price"],
            tp2_price=row["tp2_price"],
            tp1_hit=bool(row["tp1_hit"]),
            strategy_tag=row["strategy_tag"] or "",
            fees_paid=row["fees_paid"],
            funding_paid=row["funding_paid"],
        )

    def delete_position(self, symbol: str) -> None:
        """Delete the active position."""
        self._conn.execute("DELETE FROM positions WHERE symbol = ?", [symbol])
        self._conn.commit()

    # -------------------------------------------------------------------------
    # Trade History
    # -------------------------------------------------------------------------

    def save_trade(self, trade: TradeRecord, symbol: str) -> int:
        """Save a completed trade."""
        cursor = self._conn.execute("""
            INSERT INTO trades (
                symbol, entry_ts, exit_ts, side, entry_price, exit_price,
                size, pnl_gross, pnl_net, fees, funding, exit_reason,
                strategy_tag, hold_minutes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            symbol,
            trade.entry_ts,
            trade.exit_ts,
            trade.side.name,
            trade.entry_price,
            trade.exit_price,
            trade.size,
            trade.pnl_gross,
            trade.pnl_net,
            trade.fees,
            trade.funding,
            trade.exit_reason.name,
            trade.strategy_tag,
            trade.hold_minutes,
        ])
        self._conn.commit()

        # Update daily P&L
        self._update_daily_pnl(trade, symbol)

        return cursor.lastrowid

    def get_trades(
        self,
        symbol: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[dict]:
        """Query trade history."""
        query = "SELECT * FROM trades WHERE symbol = ?"
        params = [symbol]

        if start_ts:
            query += " AND exit_ts >= ?"
            params.append(start_ts)

        if end_ts:
            query += " AND exit_ts <= ?"
            params.append(end_ts)

        query += " ORDER BY exit_ts DESC"

        if limit:
            query += f" LIMIT {limit}"

        cursor = self._conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def get_trade_stats(
        self,
        symbol: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
    ) -> dict:
        """Calculate trading statistics."""
        query = """
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN pnl_net > 0 THEN 1 ELSE 0 END) as winners,
                SUM(CASE WHEN pnl_net <= 0 THEN 1 ELSE 0 END) as losers,
                SUM(pnl_net) as total_pnl,
                AVG(pnl_net) as avg_pnl,
                AVG(CASE WHEN pnl_net > 0 THEN pnl_net ELSE NULL END) as avg_winner,
                AVG(CASE WHEN pnl_net <= 0 THEN pnl_net ELSE NULL END) as avg_loser,
                SUM(fees) as total_fees,
                SUM(funding) as total_funding,
                AVG(hold_minutes) as avg_hold_minutes
            FROM trades
            WHERE symbol = ?
        """
        params = [symbol]

        if start_ts:
            query = query.replace("WHERE symbol = ?", "WHERE symbol = ? AND exit_ts >= ?")
            params.append(start_ts)

        if end_ts:
            if start_ts:
                query += " AND exit_ts <= ?"
            else:
                query = query.replace("WHERE symbol = ?", "WHERE symbol = ? AND exit_ts <= ?")
            params.append(end_ts)

        cursor = self._conn.execute(query, params)
        row = cursor.fetchone()

        if not row or row["total_trades"] == 0:
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "avg_pnl": 0.0,
            }

        return {
            "total_trades": row["total_trades"],
            "winners": row["winners"] or 0,
            "losers": row["losers"] or 0,
            "win_rate": (row["winners"] or 0) / row["total_trades"],
            "total_pnl": row["total_pnl"] or 0,
            "avg_pnl": row["avg_pnl"] or 0,
            "avg_winner": row["avg_winner"] or 0,
            "avg_loser": row["avg_loser"] or 0,
            "total_fees": row["total_fees"] or 0,
            "total_funding": row["total_funding"] or 0,
            "avg_hold_minutes": row["avg_hold_minutes"] or 0,
        }

    # -------------------------------------------------------------------------
    # Daily P&L
    # -------------------------------------------------------------------------

    def _update_daily_pnl(self, trade: TradeRecord, symbol: str) -> None:
        """Update daily P&L after a trade."""
        date = datetime.utcfromtimestamp(trade.exit_ts / 1000).strftime("%Y-%m-%d")
        is_winner = trade.pnl_net > 0

        self._conn.execute("""
            INSERT INTO daily_pnl (date, symbol, realized_pnl, trades_count, win_count, loss_count, fees_total, funding_total)
            VALUES (?, ?, ?, 1, ?, ?, ?, ?)
            ON CONFLICT(date, symbol) DO UPDATE SET
                realized_pnl = realized_pnl + ?,
                trades_count = trades_count + 1,
                win_count = win_count + ?,
                loss_count = loss_count + ?,
                fees_total = fees_total + ?,
                funding_total = funding_total + ?
        """, [
            date, symbol, trade.pnl_net, 1 if is_winner else 0, 0 if is_winner else 1,
            trade.fees, trade.funding,
            trade.pnl_net, 1 if is_winner else 0, 0 if is_winner else 1,
            trade.fees, trade.funding,
        ])
        self._conn.commit()

    def get_daily_pnl(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[dict]:
        """Get daily P&L history."""
        query = "SELECT * FROM daily_pnl WHERE symbol = ?"
        params = [symbol]

        if start_date:
            query += " AND date >= ?"
            params.append(start_date)

        if end_date:
            query += " AND date <= ?"
            params.append(end_date)

        query += " ORDER BY date ASC"

        cursor = self._conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def get_equity_curve(self, symbol: str, initial_capital: float) -> List[dict]:
        """Calculate equity curve from trade history."""
        trades = self.get_trades(symbol)
        trades.reverse()  # Oldest first

        equity = initial_capital
        curve = [{"ts": 0, "equity": equity}]

        for trade in trades:
            equity += trade["pnl_net"]
            curve.append({
                "ts": trade["exit_ts"],
                "equity": equity,
            })

        return curve
