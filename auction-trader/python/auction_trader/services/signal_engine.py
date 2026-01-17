"""Signal engine implementing Auction Market Theory trading setups.

Three core setups:
1. Break-in: Failed auction returning to value (mean reversion)
2. Breakout: Acceptance outside value after k consecutive closes (trend continuation)
3. Failed Breakout: Fakeout reversal back into value
"""

from dataclasses import dataclass, field
from typing import Optional, List
from enum import Enum

from ..config import Config, SignalConfig, OrderFlowConfig
from ..models.types import (
    Features1m,
    Signal,
    SignalType,
    Action,
    ValueArea,
    OrderFlowMetrics,
    AcceptanceState,
)


class PriceZone(Enum):
    """Price location relative to Value Area."""
    INSIDE_VA = "inside"
    ABOVE_VAH = "above"
    BELOW_VAL = "below"


@dataclass
class SignalCandidate:
    """A potential signal before priority resolution."""
    signal_type: SignalType
    stop_price: float
    tp1_price: float
    tp2_price: float
    reason: str
    confidence: float = 1.0


class SignalEngine:
    """Signal engine for generating trading signals based on AMT.

    Implements three setups:
    - Break-in: Price returning to VA from outside (mean reversion)
    - Breakout: k consecutive closes outside VA with OF confirmation
    - Failed Breakout: Price probed outside but failed to accept

    Signal priority: Break-in > Failed Breakout > Breakout
    """

    def __init__(self, config: Config):
        self.config = config
        self.signal_config = config.signal
        self.of_config = config.order_flow
        self.risk_config = config.risk

        # Acceptance tracking state
        self.acceptance = AcceptanceState()

        # Previous features for retest detection
        self.prev_features: Optional[Features1m] = None
        self.prev_zone: Optional[PriceZone] = None

        # Cooldown tracking
        self.last_signal_ts: Optional[int] = None

    def process(self, features: Features1m) -> Signal:
        """Process features and generate signal.

        Args:
            features: Complete feature set for current minute

        Returns:
            Signal with action, stops, and targets
        """
        # Check for invalid VA
        if not features.va.is_valid:
            return self._hold_signal(features, "Invalid VA")

        # Check cooldown
        if self._in_cooldown(features.ts_min):
            return self._hold_signal(features, "In cooldown")

        # Determine price zone
        zone = self._get_price_zone(features.mid_close, features.va)

        # Update acceptance state
        self._update_acceptance(zone, features)

        # Collect candidate signals
        candidates: List[SignalCandidate] = []

        # Check for Break-in signals
        breakin = self._check_breakin(features, zone)
        if breakin:
            candidates.append(breakin)

        # Check for Failed Breakout signals
        failed = self._check_failed_breakout(features, zone)
        if failed:
            candidates.append(failed)

        # Check for Breakout signals
        breakout = self._check_breakout(features, zone)
        if breakout:
            candidates.append(breakout)

        # Store for next iteration
        self.prev_features = features
        self.prev_zone = zone

        # Resolve priority and return signal
        if not candidates:
            return self._hold_signal(features, "No setup detected")

        # Sort by priority (lower = higher priority)
        candidates.sort(key=lambda c: c.signal_type.priority)
        winner = candidates[0]

        # Record signal time for cooldown
        self.last_signal_ts = features.ts_min

        return Signal(
            ts_min=features.ts_min,
            signal_type=winner.signal_type,
            action=self._signal_to_action(winner.signal_type),
            stop_price=winner.stop_price,
            tp1_price=winner.tp1_price,
            tp2_price=winner.tp2_price,
            strategy_tag=self._get_strategy_tag(winner.signal_type),
            confidence=winner.confidence,
            reason=winner.reason,
            features_snapshot=features,
        )

    def _get_price_zone(self, price: float, va: ValueArea) -> PriceZone:
        """Determine which zone the price is in."""
        if price > va.vah:
            return PriceZone.ABOVE_VAH
        elif price < va.val:
            return PriceZone.BELOW_VAL
        else:
            return PriceZone.INSIDE_VA

    def _update_acceptance(self, zone: PriceZone, features: Features1m) -> None:
        """Update acceptance state based on current zone."""
        va = features.va

        if zone == PriceZone.ABOVE_VAH:
            # Track consecutive closes above VAH
            if self.acceptance.consecutive_above_vah == 0:
                # Start new sequence
                self.acceptance.locked_vah = va.vah
                self.acceptance.sequence_start_ts = features.ts_min
            self.acceptance.consecutive_above_vah += 1
            # Reset below sequence
            self.acceptance.reset_below()

        elif zone == PriceZone.BELOW_VAL:
            # Track consecutive closes below VAL
            if self.acceptance.consecutive_below_val == 0:
                # Start new sequence
                self.acceptance.locked_val = va.val
                self.acceptance.sequence_start_ts = features.ts_min
            self.acceptance.consecutive_below_val += 1
            # Reset above sequence
            self.acceptance.reset_above()

        else:
            # Inside VA - reset both sequences
            self.acceptance.reset_above()
            self.acceptance.reset_below()

    def _check_breakin(
        self, features: Features1m, zone: PriceZone
    ) -> Optional[SignalCandidate]:
        """Check for Break-in setup (mean reversion into VA).

        Break-in triggers when price re-enters VA from outside
        with confirming order flow.
        """
        if self.prev_zone is None:
            return None

        va = features.va
        of = features.order_flow

        # Long Break-in: Was below VAL, now inside VA
        if self.prev_zone == PriceZone.BELOW_VAL and zone == PriceZone.INSIDE_VA:
            if self._check_of_entry_long(of, features):
                return SignalCandidate(
                    signal_type=SignalType.BREAKIN_LONG,
                    stop_price=va.val - self._stop_buffer(features),
                    tp1_price=va.poc,
                    tp2_price=va.vah,
                    reason=f"Break-in long: price returned to VA from below VAL={va.val:.2f}",
                )

        # Short Break-in: Was above VAH, now inside VA
        if self.prev_zone == PriceZone.ABOVE_VAH and zone == PriceZone.INSIDE_VA:
            if self._check_of_entry_short(of, features):
                return SignalCandidate(
                    signal_type=SignalType.BREAKIN_SHORT,
                    stop_price=va.vah + self._stop_buffer(features),
                    tp1_price=va.poc,
                    tp2_price=va.val,
                    reason=f"Break-in short: price returned to VA from above VAH={va.vah:.2f}",
                )

        return None

    def _check_failed_breakout(
        self, features: Features1m, zone: PriceZone
    ) -> Optional[SignalCandidate]:
        """Check for Failed Breakout setup (fakeout reversal).

        Failed breakout triggers when price was outside VA for 1 to k-1 bars
        but returns inside without achieving acceptance.
        """
        va = features.va
        of = features.order_flow
        k = self.signal_config.accept_outside_k

        # Long Failed Breakout: Was below VAL (1 to k-1 bars), now inside
        if (zone == PriceZone.INSIDE_VA and
            self.prev_zone == PriceZone.BELOW_VAL and
            1 <= self.acceptance.consecutive_below_val < k):

            if self._check_of_fail_long(of, features):
                return SignalCandidate(
                    signal_type=SignalType.FAILED_BREAKOUT_LONG,
                    stop_price=va.val - self._stop_buffer(features),
                    tp1_price=va.poc,
                    tp2_price=va.vah,
                    reason=f"Failed breakout long: {self.acceptance.consecutive_below_val} bars below VAL, now returning",
                )

        # Short Failed Breakout: Was above VAH (1 to k-1 bars), now inside
        if (zone == PriceZone.INSIDE_VA and
            self.prev_zone == PriceZone.ABOVE_VAH and
            1 <= self.acceptance.consecutive_above_vah < k):

            if self._check_of_fail_short(of, features):
                return SignalCandidate(
                    signal_type=SignalType.FAILED_BREAKOUT_SHORT,
                    stop_price=va.vah + self._stop_buffer(features),
                    tp1_price=va.poc,
                    tp2_price=va.val,
                    reason=f"Failed breakout short: {self.acceptance.consecutive_above_vah} bars above VAH, now returning",
                )

        return None

    def _check_breakout(
        self, features: Features1m, zone: PriceZone
    ) -> Optional[SignalCandidate]:
        """Check for Breakout setup (trend continuation).

        Breakout triggers when price has closed outside VA for k
        consecutive bars (acceptance achieved).
        """
        va = features.va
        of = features.order_flow
        k = self.signal_config.accept_outside_k

        # Long Breakout: k consecutive closes above VAH
        if (zone == PriceZone.ABOVE_VAH and
            self.acceptance.consecutive_above_vah >= k):

            if self._check_of_breakout_long(of, features):
                # Use locked VAH as stop reference
                stop_ref = self.acceptance.locked_vah or va.vah
                return SignalCandidate(
                    signal_type=SignalType.BREAKOUT_LONG,
                    stop_price=stop_ref - self._stop_buffer(features),
                    tp1_price=features.mid_close + (features.mid_close - stop_ref),  # 1R
                    tp2_price=features.mid_close + 2 * (features.mid_close - stop_ref),  # 2R
                    reason=f"Breakout long: {self.acceptance.consecutive_above_vah} bars above VAH (accepted)",
                    confidence=0.9,  # Slightly lower confidence for breakouts
                )

        # Short Breakout: k consecutive closes below VAL
        if (zone == PriceZone.BELOW_VAL and
            self.acceptance.consecutive_below_val >= k):

            if self._check_of_breakout_short(of, features):
                # Use locked VAL as stop reference
                stop_ref = self.acceptance.locked_val or va.val
                return SignalCandidate(
                    signal_type=SignalType.BREAKOUT_SHORT,
                    stop_price=stop_ref + self._stop_buffer(features),
                    tp1_price=features.mid_close - (stop_ref - features.mid_close),  # 1R
                    tp2_price=features.mid_close - 2 * (stop_ref - features.mid_close),  # 2R
                    reason=f"Breakout short: {self.acceptance.consecutive_below_val} bars below VAL (accepted)",
                    confidence=0.9,
                )

        return None

    def _check_of_condition(
        self,
        of: OrderFlowMetrics,
        features: Features1m,
        of_threshold: float,
        of_norm_threshold: float,
        qimb_threshold: float,
        is_long: bool,
    ) -> bool:
        """Check order flow conditions for a signal.

        Args:
            of: Order flow metrics
            features: Current features
            of_threshold: Raw OF threshold (absolute value)
            of_norm_threshold: Normalized OF threshold (absolute value)
            qimb_threshold: QIMB threshold (absolute value)
            is_long: True for long signals, False for short signals
        """
        if is_long:
            of_ok = of.of_1m >= of_threshold or of.of_norm_1m >= of_norm_threshold
            qimb_ok = not self.of_config.use_qimb or features.qimb_ema >= qimb_threshold
        else:
            of_ok = of.of_1m <= -of_threshold or of.of_norm_1m <= -of_norm_threshold
            qimb_ok = not self.of_config.use_qimb or features.qimb_ema <= -qimb_threshold
        return of_ok and qimb_ok

    def _check_of_entry_long(self, of: OrderFlowMetrics, features: Features1m) -> bool:
        """Check order flow conditions for long entry (break-in)."""
        return self._check_of_condition(
            of, features,
            self.signal_config.of_entry_min,
            self.signal_config.of_entry_min_norm,
            self.of_config.qimb_entry_min,
            is_long=True,
        )

    def _check_of_entry_short(self, of: OrderFlowMetrics, features: Features1m) -> bool:
        """Check order flow conditions for short entry (break-in)."""
        return self._check_of_condition(
            of, features,
            self.signal_config.of_entry_min,
            self.signal_config.of_entry_min_norm,
            self.of_config.qimb_entry_min,
            is_long=False,
        )

    def _check_of_breakout_long(self, of: OrderFlowMetrics, features: Features1m) -> bool:
        """Check order flow conditions for long breakout."""
        return self._check_of_condition(
            of, features,
            self.signal_config.of_breakout_min,
            self.signal_config.of_breakout_min_norm,
            self.of_config.qimb_breakout_min,
            is_long=True,
        )

    def _check_of_breakout_short(self, of: OrderFlowMetrics, features: Features1m) -> bool:
        """Check order flow conditions for short breakout."""
        return self._check_of_condition(
            of, features,
            self.signal_config.of_breakout_min,
            self.signal_config.of_breakout_min_norm,
            self.of_config.qimb_breakout_min,
            is_long=False,
        )

    def _check_of_fail_long(self, of: OrderFlowMetrics, features: Features1m) -> bool:
        """Check order flow conditions for failed breakout long."""
        return self._check_of_condition(
            of, features,
            self.signal_config.of_fail_max,
            self.signal_config.of_fail_max_norm,
            self.of_config.qimb_fail_max,
            is_long=True,
        )

    def _check_of_fail_short(self, of: OrderFlowMetrics, features: Features1m) -> bool:
        """Check order flow conditions for failed breakout short."""
        return self._check_of_condition(
            of, features,
            self.signal_config.of_fail_max,
            self.signal_config.of_fail_max_norm,
            self.of_config.qimb_fail_max,
            is_long=False,
        )

    def _stop_buffer(self, features: Features1m) -> float:
        """Calculate stop buffer in price terms."""
        tick_size = self.config.instrument.tick_size
        buffer_ticks = self.risk_config.stop_buffer_ticks
        return buffer_ticks * tick_size

    def _signal_to_action(self, signal_type: SignalType) -> Action:
        """Convert signal type to action."""
        if signal_type.is_long():
            return Action.ENTER_LONG
        else:
            return Action.ENTER_SHORT

    def _get_strategy_tag(self, signal_type: SignalType) -> str:
        """Get strategy tag for position tracking."""
        mapping = {
            SignalType.BREAKIN_LONG: "breakin_long",
            SignalType.BREAKIN_SHORT: "breakin_short",
            SignalType.BREAKOUT_LONG: "breakout_long",
            SignalType.BREAKOUT_SHORT: "breakout_short",
            SignalType.FAILED_BREAKOUT_LONG: "failed_long",
            SignalType.FAILED_BREAKOUT_SHORT: "failed_short",
        }
        return mapping.get(signal_type, "unknown")

    def _in_cooldown(self, current_ts: int) -> bool:
        """Check if we're in cooldown period."""
        if self.last_signal_ts is None:
            return False

        cooldown_ms = self.risk_config.cooldown_minutes * 60_000
        return (current_ts - self.last_signal_ts) < cooldown_ms

    def _hold_signal(self, features: Features1m, reason: str) -> Signal:
        """Generate a HOLD signal."""
        return Signal(
            ts_min=features.ts_min,
            signal_type=None,
            action=Action.HOLD,
            reason=reason,
            features_snapshot=features,
        )

    def reset(self) -> None:
        """Reset engine state (for backtesting)."""
        self.acceptance = AcceptanceState()
        self.prev_features = None
        self.prev_zone = None
        self.last_signal_ts = None
