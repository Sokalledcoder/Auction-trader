"""
Auction Trader - BTC Perpetual Trading System

A systematic trading system implementing Auction Market Theory (AMT)
mechanics on BTC perpetuals via Bybit.

Core setups:
- Break-in: Failed auction returning to value (mean reversion)
- Breakout: Acceptance outside value (trend continuation)
- Failed breakout: Fakeout reversal back into value
"""

__version__ = "0.1.0"
__author__ = "auction-trader"

from .config import Config, load_config
from .models.types import (
    Trade,
    Quote,
    Bar1m,
    Features1m,
    Signal,
    SignalType,
    Action,
    PositionSide,
    Position,
    ValueArea,
    OrderFlowMetrics,
)
from .orchestrator import Orchestrator, TradingMode
from .services import (
    SignalEngine,
    PositionManager,
    BybitCollector,
    BybitExecutor,
    PaperExecutor,
)

__all__ = [
    # Config
    "Config",
    "load_config",
    # Types
    "Trade",
    "Quote",
    "Bar1m",
    "Features1m",
    "Signal",
    "SignalType",
    "Action",
    "PositionSide",
    "Position",
    "ValueArea",
    "OrderFlowMetrics",
    # Orchestrator
    "Orchestrator",
    "TradingMode",
    # Services
    "SignalEngine",
    "PositionManager",
    "BybitCollector",
    "BybitExecutor",
    "PaperExecutor",
]
