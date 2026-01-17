"""Features storage using DuckDB.

Stores computed features for analysis and backtesting.
"""

import logging
from pathlib import Path
from typing import Optional, List
import duckdb

from ..config import DatabaseConfig
from ..models.types import Features1m, ValueArea, OrderFlowMetrics

logger = logging.getLogger(__name__)


class FeaturesStore:
    """DuckDB storage for computed features."""

    def __init__(self, config: DatabaseConfig):
        self.config = config
        self.db_path = Path(config.data_dir) / config.features_db
        self._conn: Optional[duckdb.DuckDBPyConnection] = None

    def connect(self) -> None:
        """Connect to the database."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = duckdb.connect(str(self.db_path))
        self._create_tables()
        logger.info(f"Connected to features store: {self.db_path}")

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def _create_tables(self) -> None:
        """Create tables if they don't exist."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS features_1m (
                ts_min BIGINT,
                symbol VARCHAR,
                mid_close DOUBLE,
                sigma_240 DOUBLE,
                bin_width DOUBLE,
                -- Value Area
                va_poc DOUBLE,
                va_vah DOUBLE,
                va_val DOUBLE,
                va_coverage DOUBLE,
                va_bin_count INTEGER,
                va_total_volume DOUBLE,
                va_is_valid BOOLEAN,
                -- Order Flow
                of_1m DOUBLE,
                of_norm_1m DOUBLE,
                of_total_volume DOUBLE,
                of_buy_volume DOUBLE,
                of_sell_volume DOUBLE,
                of_ambiguous_volume DOUBLE,
                of_ambiguous_frac DOUBLE,
                -- Quote Imbalance
                qimb_close DOUBLE,
                qimb_ema DOUBLE,
                spread_avg_60m DOUBLE,
                PRIMARY KEY (ts_min, symbol)
            )
        """)

        # Create index for time-based queries
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_features_ts
            ON features_1m (ts_min)
        """)

    def insert_features(self, features: Features1m, symbol: str) -> None:
        """Insert computed features."""
        va = features.va
        of = features.order_flow

        self._conn.execute("""
            INSERT OR REPLACE INTO features_1m VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?
            )
        """, [
            features.ts_min, symbol, features.mid_close, features.sigma_240, features.bin_width,
            va.poc, va.vah, va.val, va.coverage, va.bin_count, va.total_volume, va.is_valid,
            of.of_1m, of.of_norm_1m, of.total_volume, of.buy_volume, of.sell_volume,
            of.ambiguous_volume, of.ambiguous_frac,
            features.qimb_close, features.qimb_ema, features.spread_avg_60m,
        ])

    def get_features(
        self,
        symbol: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[Features1m]:
        """Query features with optional filters."""
        query = "SELECT * FROM features_1m WHERE symbol = ?"
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

        features_list = []
        for row in result:
            va = ValueArea(
                poc=row[5],
                vah=row[6],
                val=row[7],
                coverage=row[8],
                bin_count=row[9],
                total_volume=row[10],
                bin_width=row[4],
                is_valid=row[11],
            )
            of = OrderFlowMetrics(
                of_1m=row[12],
                of_norm_1m=row[13],
                total_volume=row[14],
                buy_volume=row[15],
                sell_volume=row[16],
                ambiguous_volume=row[17],
                ambiguous_frac=row[18],
            )
            features = Features1m(
                ts_min=row[0],
                mid_close=row[2],
                sigma_240=row[3],
                bin_width=row[4],
                va=va,
                order_flow=of,
                qimb_close=row[19],
                qimb_ema=row[20],
                spread_avg_60m=row[21],
            )
            features_list.append(features)

        return features_list

    def get_latest_features(self, symbol: str) -> Optional[Features1m]:
        """Get the most recent features."""
        features = self.get_features(symbol, limit=1)
        return features[0] if features else None

    def get_va_history(
        self,
        symbol: str,
        start_ts: int,
        end_ts: int,
    ) -> List[dict]:
        """Get Value Area history for charting."""
        result = self._conn.execute("""
            SELECT ts_min, va_poc, va_vah, va_val, va_coverage, va_is_valid
            FROM features_1m
            WHERE symbol = ? AND ts_min >= ? AND ts_min <= ?
            ORDER BY ts_min ASC
        """, [symbol, start_ts, end_ts]).fetchall()

        return [
            {
                "ts_min": row[0],
                "poc": row[1],
                "vah": row[2],
                "val": row[3],
                "coverage": row[4],
                "is_valid": row[5],
            }
            for row in result
        ]

    def vacuum(self) -> None:
        """Optimize the database."""
        self._conn.execute("VACUUM")
