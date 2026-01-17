"""Raw data storage using DuckDB.

Stores:
- Raw trades
- Raw quotes
- 1-minute bars
"""

import logging
from pathlib import Path
from typing import Optional, List
import duckdb

from ..config import DatabaseConfig
from ..models.types import Trade, Quote, Bar1m

logger = logging.getLogger(__name__)


class RawStore:
    """DuckDB storage for raw market data."""

    def __init__(self, config: DatabaseConfig):
        self.config = config
        self.db_path = Path(config.data_dir) / config.raw_db
        self._conn: Optional[duckdb.DuckDBPyConnection] = None

    def connect(self) -> None:
        """Connect to the database."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = duckdb.connect(str(self.db_path))
        self._create_tables()
        logger.info(f"Connected to raw store: {self.db_path}")

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def _create_tables(self) -> None:
        """Create tables if they don't exist."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                ts_ms BIGINT,
                price DOUBLE,
                size DOUBLE,
                symbol VARCHAR,
                PRIMARY KEY (ts_ms, symbol)
            )
        """)

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS quotes (
                ts_ms BIGINT,
                bid_px DOUBLE,
                bid_sz DOUBLE,
                ask_px DOUBLE,
                ask_sz DOUBLE,
                symbol VARCHAR,
                PRIMARY KEY (ts_ms, symbol)
            )
        """)

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS bars_1m (
                ts_min BIGINT,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume DOUBLE,
                vwap DOUBLE,
                trade_count INTEGER,
                bid_px_close DOUBLE,
                ask_px_close DOUBLE,
                bid_sz_close DOUBLE,
                ask_sz_close DOUBLE,
                symbol VARCHAR,
                PRIMARY KEY (ts_min, symbol)
            )
        """)

    def insert_trade(self, trade: Trade, symbol: str) -> None:
        """Insert a single trade."""
        self._conn.execute(
            "INSERT OR REPLACE INTO trades VALUES (?, ?, ?, ?)",
            [trade.ts_ms, trade.price, trade.size, symbol]
        )

    def insert_trades(self, trades: List[Trade], symbol: str) -> None:
        """Insert multiple trades."""
        data = [(t.ts_ms, t.price, t.size, symbol) for t in trades]
        self._conn.executemany(
            "INSERT OR REPLACE INTO trades VALUES (?, ?, ?, ?)",
            data
        )

    def insert_quote(self, quote: Quote, symbol: str) -> None:
        """Insert a single quote."""
        self._conn.execute(
            "INSERT OR REPLACE INTO quotes VALUES (?, ?, ?, ?, ?, ?)",
            [quote.ts_ms, quote.bid_px, quote.bid_sz, quote.ask_px, quote.ask_sz, symbol]
        )

    def insert_bar(self, bar: Bar1m, symbol: str) -> None:
        """Insert a 1-minute bar."""
        self._conn.execute(
            "INSERT OR REPLACE INTO bars_1m VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                bar.ts_min, bar.open, bar.high, bar.low, bar.close,
                bar.volume, bar.vwap, bar.trade_count,
                bar.bid_px_close, bar.ask_px_close, bar.bid_sz_close, bar.ask_sz_close,
                symbol
            ]
        )

    def get_bars(
        self,
        symbol: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[Bar1m]:
        """Query bars with optional filters."""
        query = "SELECT * FROM bars_1m WHERE symbol = ?"
        params = [symbol]

        if start_ts:
            query += " AND ts_min >= ?"
            params.append(start_ts)

        if end_ts:
            query += " AND ts_min <= ?"
            params.append(end_ts)

        query += " ORDER BY ts_min ASC"

        if limit:
            query += f" LIMIT {limit}"

        result = self._conn.execute(query, params).fetchall()

        return [
            Bar1m(
                ts_min=row[0],
                open=row[1],
                high=row[2],
                low=row[3],
                close=row[4],
                volume=row[5],
                vwap=row[6],
                trade_count=row[7],
                bid_px_close=row[8],
                ask_px_close=row[9],
                bid_sz_close=row[10],
                ask_sz_close=row[11],
            )
            for row in result
        ]

    def get_trades(
        self,
        symbol: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[Trade]:
        """Query trades with optional filters."""
        query = "SELECT ts_ms, price, size FROM trades WHERE symbol = ?"
        params = [symbol]

        if start_ts:
            query += " AND ts_ms >= ?"
            params.append(start_ts)

        if end_ts:
            query += " AND ts_ms <= ?"
            params.append(end_ts)

        query += " ORDER BY ts_ms ASC"

        if limit:
            query += f" LIMIT {limit}"

        result = self._conn.execute(query, params).fetchall()

        return [Trade(ts_ms=row[0], price=row[1], size=row[2]) for row in result]

    def get_latest_bar_ts(self, symbol: str) -> Optional[int]:
        """Get the timestamp of the latest bar."""
        result = self._conn.execute(
            "SELECT MAX(ts_min) FROM bars_1m WHERE symbol = ?",
            [symbol]
        ).fetchone()

        return result[0] if result and result[0] else None

    def vacuum(self) -> None:
        """Optimize the database."""
        self._conn.execute("VACUUM")
