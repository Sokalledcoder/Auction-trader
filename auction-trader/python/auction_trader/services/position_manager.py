"""Position manager for tracking and managing open positions.

Handles:
- Position entry with proper sizing
- Partial exits at TP1 (30%) and TP2 (70%)
- Stop loss management
- Breakeven stop after TP1
- Time-based exits
- Daily loss limits
"""

from dataclasses import dataclass, field
from typing import Optional, List, Tuple
from enum import Enum, auto

from ..config import Config, SizingConfig, RiskConfig, ExecutionConfig
from ..models.types import (
    Signal,
    SignalType,
    Action,
    Position,
    PositionSide,
    Features1m,
)


class ExitReason(Enum):
    """Reason for position exit."""
    STOP_LOSS = auto()
    TP1 = auto()
    TP2 = auto()
    TIME_STOP = auto()
    FLIP_SIGNAL = auto()
    DAILY_LOSS = auto()
    MANUAL = auto()


@dataclass
class FillResult:
    """Result of an order fill."""
    price: float
    size: float
    fee: float
    slippage: float


@dataclass
class TradeRecord:
    """Record of a completed trade."""
    entry_ts: int
    exit_ts: int
    side: PositionSide
    entry_price: float
    exit_price: float
    size: float
    pnl_gross: float
    pnl_net: float
    fees: float
    funding: float
    exit_reason: ExitReason
    strategy_tag: str
    hold_minutes: int


class PositionManager:
    """Manages open positions and executes exits.

    Features:
    - Risk-based position sizing
    - Partial exit management (30/70 split)
    - Breakeven stop after TP1
    - Time-based exits with profitability extension
    - Daily loss limit tracking
    """

    def __init__(self, config: Config, initial_capital: float):
        self.config = config
        self.sizing = config.sizing
        self.risk = config.risk
        self.execution = config.execution

        # Capital tracking
        self.initial_capital = initial_capital
        self.available_margin = initial_capital
        self.daily_pnl = 0.0
        self.daily_start_ts: Optional[int] = None

        # Position state
        self.position: Optional[Position] = None

        # Trade history
        self.trades: List[TradeRecord] = []

    def process_signal(
        self,
        signal: Signal,
        current_price: float,
        current_ts: int,
    ) -> Optional[str]:
        """Process a signal and update position.

        Args:
            signal: Signal from signal engine
            current_price: Current mid price for execution
            current_ts: Current timestamp in ms

        Returns:
            Action description or None
        """
        # Check daily loss limit first
        if self._check_daily_loss_limit():
            if self.position:
                return self._close_position(
                    current_price, current_ts, ExitReason.DAILY_LOSS
                )
            return "Daily loss limit reached - no new trades"

        # Reset daily tracking if new day
        self._check_daily_reset(current_ts)

        action = signal.action

        if action == Action.HOLD:
            return None

        if action == Action.EXIT:
            if self.position:
                return self._close_position(
                    current_price, current_ts, ExitReason.MANUAL
                )
            return None

        # Entry signals
        if action in (Action.ENTER_LONG, Action.ENTER_SHORT):
            return self._handle_entry(signal, current_price, current_ts)

        return None

    def check_exits(
        self,
        high: float,
        low: float,
        current_price: float,
        current_ts: int,
    ) -> Optional[str]:
        """Check for stop/target/time exits.

        Args:
            high: High price of current bar
            low: Low price of current bar
            current_price: Current mid price
            current_ts: Current timestamp

        Returns:
            Exit description or None
        """
        if not self.position:
            return None

        # Check stop loss first (highest priority)
        stop_hit = self._check_stop(high, low)
        if stop_hit:
            return self._close_position(
                self.position.stop_price, current_ts, ExitReason.STOP_LOSS
            )

        # Check TP1 (partial exit)
        tp1_hit = self._check_tp1(high, low)
        if tp1_hit:
            return self._partial_exit_tp1(current_ts)

        # Check TP2 (full exit)
        tp2_hit = self._check_tp2(high, low)
        if tp2_hit:
            return self._close_position(
                self.position.tp2_price, current_ts, ExitReason.TP2
            )

        # Check time stop
        time_exit = self._check_time_stop(current_price, current_ts)
        if time_exit:
            return self._close_position(
                current_price, current_ts, ExitReason.TIME_STOP
            )

        return None

    def _handle_entry(
        self,
        signal: Signal,
        current_price: float,
        current_ts: int,
    ) -> Optional[str]:
        """Handle entry signal."""
        side = PositionSide.LONG if signal.action == Action.ENTER_LONG else PositionSide.SHORT

        # Check for existing position
        if self.position:
            if self.config.signal.enable_flip_on_signal:
                # Close existing and flip
                if self.position.side != side:
                    self._close_position(
                        current_price, current_ts, ExitReason.FLIP_SIGNAL
                    )
                else:
                    # Same direction, ignore
                    return None
            else:
                # Don't flip, ignore signal
                return None

        # Calculate position size
        size = self._calculate_size(
            entry_price=current_price,
            stop_price=signal.stop_price,
        )

        if size <= 0:
            return "Position size calculation resulted in zero"

        # Calculate entry fee
        entry_fee = self._calculate_fee(current_price, size, is_entry=True)

        # Create position
        self.position = Position(
            entry_ts=current_ts,
            side=side,
            entry_price=current_price,
            size=size,
            original_size=size,
            stop_price=signal.stop_price,
            tp1_price=signal.tp1_price,
            tp2_price=signal.tp2_price,
            tp1_hit=False,
            strategy_tag=signal.strategy_tag,
            fees_paid=entry_fee,
            funding_paid=0.0,
        )

        return f"Entered {side.name} @ {current_price:.2f}, size={size:.6f}, stop={signal.stop_price:.2f}"

    def _calculate_size(self, entry_price: float, stop_price: float) -> float:
        """Calculate position size based on risk.

        Formula: size = (available_margin * risk_pct) / abs(entry - stop)

        Then constrained by max leverage.
        """
        risk_amount = self.available_margin * self.sizing.risk_pct
        stop_distance = abs(entry_price - stop_price)

        if stop_distance <= 0:
            return 0.0

        # Base size from risk
        size = risk_amount / stop_distance

        # Check leverage constraint
        notional = size * entry_price
        leverage = notional / self.available_margin

        if leverage > self.sizing.max_leverage:
            # Reduce size to fit within leverage limit
            max_notional = self.available_margin * self.sizing.max_leverage
            size = max_notional / entry_price

        return size

    def _price_crossed(
        self,
        high: float,
        low: float,
        target_price: float,
        above_for_long: bool,
    ) -> bool:
        """Check if price crossed a target level.

        Args:
            high: Bar high price
            low: Bar low price
            target_price: Price level to check
            above_for_long: If True, longs trigger when price goes above target;
                           if False, longs trigger when price goes below target
        """
        if not self.position:
            return False
        is_long = self.position.side == PositionSide.LONG
        if above_for_long:
            return high >= target_price if is_long else low <= target_price
        else:
            return low <= target_price if is_long else high >= target_price

    def _check_stop(self, high: float, low: float) -> bool:
        """Check if stop loss was hit."""
        if not self.position:
            return False
        return self._price_crossed(high, low, self.position.stop_price, above_for_long=False)

    def _check_tp1(self, high: float, low: float) -> bool:
        """Check if TP1 was hit."""
        if not self.position or self.position.tp1_hit or self.position.tp1_price is None:
            return False
        return self._price_crossed(high, low, self.position.tp1_price, above_for_long=True)

    def _check_tp2(self, high: float, low: float) -> bool:
        """Check if TP2 was hit."""
        if not self.position or self.position.tp2_price is None:
            return False
        return self._price_crossed(high, low, self.position.tp2_price, above_for_long=True)

    def _check_time_stop(self, current_price: float, current_ts: int) -> bool:
        """Check time-based exit condition."""
        if not self.position:
            return False

        hold_ms = current_ts - self.position.entry_ts
        max_hold_ms = self.risk.max_hold_minutes * 60_000

        if hold_ms < max_hold_ms:
            return False

        # Time limit reached - check if we should extend
        if self.risk.extend_if_profitable:
            if self.position.is_profitable(current_price):
                # Extend - don't exit yet
                return False

        return True

    def _partial_exit_tp1(self, current_ts: int) -> str:
        """Execute partial exit at TP1."""
        if not self.position or self.position.tp1_price is None:
            return "No position or TP1"

        # Calculate partial size (30%)
        partial_size = self.position.original_size * self.sizing.tp1_pct
        exit_price = self.position.tp1_price

        # Calculate PnL for partial
        price_diff = exit_price - self.position.entry_price
        if self.position.side == PositionSide.SHORT:
            price_diff = -price_diff
        partial_pnl = price_diff * partial_size

        # Calculate exit fee
        exit_fee = self._calculate_fee(exit_price, partial_size, is_entry=False)

        # Update position
        self.position.size -= partial_size
        self.position.tp1_hit = True
        self.position.fees_paid += exit_fee

        # Move stop to breakeven if configured
        if self.sizing.move_stop_to_breakeven_after_tp1:
            self.position.stop_price = self.position.entry_price

        # Update capital
        self.available_margin += partial_pnl - exit_fee
        self.daily_pnl += partial_pnl - exit_fee

        return f"TP1 hit: closed {self.sizing.tp1_pct*100:.0f}% @ {exit_price:.2f}, PnL={partial_pnl:.2f}, stop moved to breakeven"

    def _close_position(
        self,
        exit_price: float,
        current_ts: int,
        reason: ExitReason,
    ) -> str:
        """Close entire remaining position."""
        if not self.position:
            return "No position to close"

        pos = self.position

        # Calculate PnL
        price_diff = exit_price - pos.entry_price
        if pos.side == PositionSide.SHORT:
            price_diff = -price_diff

        gross_pnl = price_diff * pos.size

        # Calculate exit fee
        exit_fee = self._calculate_fee(exit_price, pos.size, is_entry=False)

        net_pnl = gross_pnl - exit_fee

        # Create trade record
        hold_minutes = (current_ts - pos.entry_ts) // 60_000

        trade = TradeRecord(
            entry_ts=pos.entry_ts,
            exit_ts=current_ts,
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            size=pos.original_size,  # Record original size
            pnl_gross=gross_pnl,
            pnl_net=net_pnl - pos.fees_paid - pos.funding_paid,
            fees=pos.fees_paid + exit_fee,
            funding=pos.funding_paid,
            exit_reason=reason,
            strategy_tag=pos.strategy_tag,
            hold_minutes=hold_minutes,
        )
        self.trades.append(trade)

        # Update capital
        self.available_margin += net_pnl
        self.daily_pnl += net_pnl

        # Clear position
        self.position = None

        return f"Closed {pos.side.name} @ {exit_price:.2f}, reason={reason.name}, PnL={net_pnl:.2f}"

    def _calculate_fee(self, price: float, size: float, is_entry: bool) -> float:
        """Calculate trading fee."""
        notional = price * size

        if is_entry and self.execution.use_limit_for_entry:
            # Maker fee for limit orders
            fee_bps = self.execution.maker_fee_bps
        else:
            # Taker fee for market orders
            fee_bps = self.execution.taker_fee_bps

        return notional * fee_bps / 10_000

    def _check_daily_loss_limit(self) -> bool:
        """Check if daily loss limit has been reached."""
        if self.risk.max_daily_loss is None:
            return False

        return self.daily_pnl <= -self.risk.max_daily_loss

    def _check_daily_reset(self, current_ts: int) -> None:
        """Reset daily tracking if new UTC day."""
        current_day = current_ts // 86_400_000  # ms per day

        if self.daily_start_ts is None:
            self.daily_start_ts = current_ts
            return

        start_day = self.daily_start_ts // 86_400_000

        if current_day > start_day:
            self.daily_pnl = 0.0
            self.daily_start_ts = current_ts

    def apply_funding(self, funding_rate: float, mark_price: float) -> Optional[str]:
        """Apply funding payment to open position.

        Args:
            funding_rate: Funding rate (positive = longs pay shorts)
            mark_price: Mark price for funding calculation

        Returns:
            Description or None
        """
        if not self.position:
            return None

        notional = self.position.size * mark_price

        # Longs pay if positive rate, shorts pay if negative
        if self.position.side == PositionSide.LONG:
            payment = notional * funding_rate
        else:
            payment = -notional * funding_rate

        self.position.funding_paid += payment
        self.available_margin -= payment

        return f"Funding: {payment:.4f}"

    @property
    def has_position(self) -> bool:
        """Check if there's an open position."""
        return self.position is not None

    @property
    def total_pnl(self) -> float:
        """Calculate total PnL from all trades."""
        return sum(t.pnl_net for t in self.trades)

    @property
    def equity(self) -> float:
        """Current equity (initial + total pnl)."""
        return self.initial_capital + self.total_pnl

    def get_stats(self) -> dict:
        """Get trading statistics."""
        if not self.trades:
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "avg_pnl": 0.0,
                "max_drawdown": 0.0,
            }

        winners = [t for t in self.trades if t.pnl_net > 0]
        losers = [t for t in self.trades if t.pnl_net <= 0]

        # Calculate drawdown
        equity_curve = [self.initial_capital]
        for t in self.trades:
            equity_curve.append(equity_curve[-1] + t.pnl_net)

        peak = equity_curve[0]
        max_dd = 0.0
        for eq in equity_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)

        return {
            "total_trades": len(self.trades),
            "winners": len(winners),
            "losers": len(losers),
            "win_rate": len(winners) / len(self.trades) if self.trades else 0,
            "total_pnl": self.total_pnl,
            "avg_pnl": self.total_pnl / len(self.trades) if self.trades else 0,
            "avg_winner": sum(t.pnl_net for t in winners) / len(winners) if winners else 0,
            "avg_loser": sum(t.pnl_net for t in losers) / len(losers) if losers else 0,
            "max_drawdown": max_dd,
            "total_fees": sum(t.fees for t in self.trades),
            "total_funding": sum(t.funding for t in self.trades),
        }

    def reset(self, initial_capital: Optional[float] = None) -> None:
        """Reset manager state (for backtesting)."""
        if initial_capital:
            self.initial_capital = initial_capital
        self.available_margin = self.initial_capital
        self.daily_pnl = 0.0
        self.daily_start_ts = None
        self.position = None
        self.trades = []
