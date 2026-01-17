"""Storage layer for the auction-trader system.

Uses:
- DuckDB for raw data and features (fast analytics)
- SQLite for signals and execution state (simple state management)
"""

from .raw_store import RawStore
from .features_store import FeaturesStore
from .signals_store import SignalsStore
from .execution_store import ExecutionStore

__all__ = [
    "RawStore",
    "FeaturesStore",
    "SignalsStore",
    "ExecutionStore",
]
