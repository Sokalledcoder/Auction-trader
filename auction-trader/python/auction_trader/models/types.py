"""Core data types for the auction-trader system."""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional
import time


class TradeSide(Enum):
    """Inferred trade side from bid/ask alignment."""
    BUY = 1
    SELL = -1
    AMBIGUOUS = 0

    @property
    def sign(self) -> int:
        return self.value


class SignalType(Enum):
    """Trading signal types."""
    BREAKIN_LONG = auto()
    BREAKIN_SHORT = auto()
    BREAKOUT_LONG = auto()
    BREAKOUT_SHORT = auto()
    FAILED_BREAKOUT_LONG = auto()
    FAILED_BREAKOUT_SHORT = auto()

    def is_long(self) -> bool:
        return self in (
            SignalType.BREAKIN_LONG,
            SignalType.BREAKOUT_LONG,
            SignalType.FAILED_BREAKOUT_LONG,
        )

    def is_short(self) -> bool:
        return not self.is_long()

    @property
    def priority(self) -> int:
        """Get priority (lower = higher priority).
        Break-in (1) > Failed breakout (2) > Breakout (3)
        """
        if self in (SignalType.BREAKIN_LONG, SignalType.BREAKIN_SHORT):
            return 1
        elif self in (SignalType.FAILED_BREAKOUT_LONG, SignalType.FAILED_BREAKOUT_SHORT):
            return 2
        else:
            return 3


class Action(Enum):
    """Trading actions."""
    ENTER_LONG = auto()
    ENTER_SHORT = auto()
    EXIT = auto()
    HOLD = auto()


class PositionSide(Enum):
    """Position side."""
    LONG = auto()
    SHORT = auto()

    @property
    def sign(self) -> float:
        return 1.0 if self == PositionSide.LONG else -1.0


@dataclass
class Trade:
    """A single trade from the exchange."""
    ts_ms: int
    price: float
    size: float


@dataclass
class Quote:
    """A Level 1 quote (best bid/ask)."""
    ts_ms: int
    bid_px: float
    bid_sz: float
    ask_px: float
    ask_sz: float

    @property
    def mid(self) -> float:
        """Calculate mid price."""
        return (self.bid_px + self.ask_px) / 2

    @property
    def spread(self) -> float:
        """Calculate spread."""
        return self.ask_px - self.bid_px

    @property
    def imbalance(self) -> float:
        """Calculate quote imbalance."""
        total = self.bid_sz + self.ask_sz
        if total > 0:
            return (self.bid_sz - self.ask_sz) / total
        return 0.0


@dataclass
class ClassifiedTrade:
    """A trade with inferred side."""
    trade: Trade
    side: TradeSide
    quote_bid_px: float
    quote_ask_px: float
    quote_staleness_ms: int

    @property
    def signed_size(self) -> float:
        """Get signed size."""
        return self.trade.size * self.side.sign


@dataclass
class Bar1m:
    """1-minute OHLCV bar with L1 snapshot at close."""
    ts_min: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: Optional[float]
    trade_count: int
    bid_px_close: float
    ask_px_close: float
    bid_sz_close: float
    ask_sz_close: float

    @property
    def mid_close(self) -> float:
        """Calculate mid price at close."""
        return (self.bid_px_close + self.ask_px_close) / 2

    @property
    def spread_close(self) -> float:
        """Calculate spread at close."""
        return self.ask_px_close - self.bid_px_close

    @property
    def qimb_close(self) -> float:
        """Calculate quote imbalance at close."""
        total = self.bid_sz_close + self.ask_sz_close
        if total > 0:
            return (self.bid_sz_close - self.ask_sz_close) / total
        return 0.0


@dataclass
class ValueArea:
    """Value Area output."""
    poc: float
    vah: float
    val: float
    coverage: float
    bin_count: int
    total_volume: float
    bin_width: float
    is_valid: bool

    @staticmethod
    def invalid() -> "ValueArea":
        """Create an invalid/empty VA."""
        return ValueArea(
            poc=0.0,
            vah=0.0,
            val=0.0,
            coverage=0.0,
            bin_count=0,
            total_volume=0.0,
            bin_width=0.0,
            is_valid=False,
        )


@dataclass
class OrderFlowMetrics:
    """Order flow metrics for a 1-minute period."""
    of_1m: float
    of_norm_1m: float
    total_volume: float
    buy_volume: float
    sell_volume: float
    ambiguous_volume: float
    ambiguous_frac: float

    def is_high_ambiguous(self, threshold: float = 0.35) -> bool:
        """Check if ambiguous fraction is above threshold."""
        return self.ambiguous_frac > threshold


@dataclass
class Features1m:
    """Complete feature set for a 1-minute period."""
    ts_min: int
    mid_close: float
    sigma_240: float
    bin_width: float
    va: ValueArea
    order_flow: OrderFlowMetrics
    qimb_close: float
    qimb_ema: float
    spread_avg_60m: float


@dataclass
class Signal:
    """Trading signal from the signal engine."""
    ts_min: int
    signal_type: Optional[SignalType]
    action: Action
    stop_price: Optional[float] = None
    tp1_price: Optional[float] = None
    tp2_price: Optional[float] = None
    size: Optional[float] = None
    strategy_tag: str = ""
    confidence: float = 1.0
    reason: str = ""
    features_snapshot: Optional[Features1m] = None


@dataclass
class Position:
    """An open position."""
    entry_ts: int
    side: PositionSide
    entry_price: float
    size: float
    original_size: float
    stop_price: float
    tp1_price: Optional[float]
    tp2_price: Optional[float]
    tp1_hit: bool = False
    strategy_tag: str = ""
    fees_paid: float = 0.0
    funding_paid: float = 0.0

    def unrealized_pnl(self, current_price: float) -> float:
        """Calculate unrealized P&L at current price."""
        price_diff = current_price - self.entry_price
        if self.side == PositionSide.SHORT:
            price_diff = -price_diff
        return price_diff * self.size - self.fees_paid - self.funding_paid

    def is_profitable(self, current_price: float, min_profit: float = 0.0) -> bool:
        """Check if position is profitable (net of fees if min_profit=0)."""
        return self.unrealized_pnl(current_price) > min_profit


@dataclass
class AcceptanceState:
    """State for tracking acceptance sequences."""
    # Breakout acceptance tracking
    consecutive_above_vah: int = 0
    consecutive_below_val: int = 0
    # Locked VA boundaries for sequences
    locked_vah: Optional[float] = None
    locked_val: Optional[float] = None
    # Timestamps
    sequence_start_ts: Optional[int] = None

    def reset_above(self):
        """Reset above VAH sequence."""
        self.consecutive_above_vah = 0
        self.locked_vah = None
        self.sequence_start_ts = None

    def reset_below(self):
        """Reset below VAL sequence."""
        self.consecutive_below_val = 0
        self.locked_val = None
        self.sequence_start_ts = None


def ts_to_minute(ts_ms: int) -> int:
    """Convert timestamp to minute boundary."""
    return (ts_ms // 60_000) * 60_000


def current_ts_ms() -> int:
    """Get current timestamp in milliseconds."""
    return int(time.time() * 1000)
