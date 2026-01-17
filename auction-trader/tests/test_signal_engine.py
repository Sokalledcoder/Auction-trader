"""Tests for the SignalEngine."""

import pytest
from auction_trader.config import Config
from auction_trader.models.types import (
    Features1m,
    Signal,
    SignalType,
    Action,
    ValueArea,
    OrderFlowMetrics,
)
from auction_trader.services.signal_engine import SignalEngine, PriceZone


class TestPriceZone:
    """Tests for price zone detection."""

    def test_price_zone_inside_va(self):
        engine = SignalEngine(Config())
        va = ValueArea(
            poc=42000.0, vah=42200.0, val=41800.0,
            coverage=0.7, bin_count=20, total_volume=1000.0,
            bin_width=10.0, is_valid=True,
        )
        # Price inside VA
        zone = engine._get_price_zone(42000.0, va)
        assert zone == PriceZone.INSIDE_VA

    def test_price_zone_above_vah(self):
        engine = SignalEngine(Config())
        va = ValueArea(
            poc=42000.0, vah=42200.0, val=41800.0,
            coverage=0.7, bin_count=20, total_volume=1000.0,
            bin_width=10.0, is_valid=True,
        )
        zone = engine._get_price_zone(42300.0, va)
        assert zone == PriceZone.ABOVE_VAH

    def test_price_zone_below_val(self):
        engine = SignalEngine(Config())
        va = ValueArea(
            poc=42000.0, vah=42200.0, val=41800.0,
            coverage=0.7, bin_count=20, total_volume=1000.0,
            bin_width=10.0, is_valid=True,
        )
        zone = engine._get_price_zone(41700.0, va)
        assert zone == PriceZone.BELOW_VAL


class TestSignalEngineBasic:
    """Basic tests for SignalEngine."""

    def test_engine_creation(self, sample_config):
        engine = SignalEngine(sample_config)
        assert engine is not None
        assert engine.acceptance.consecutive_above_vah == 0

    def test_hold_on_invalid_va(self, sample_config):
        engine = SignalEngine(sample_config)
        invalid_va = ValueArea.invalid()
        features = Features1m(
            ts_min=1000,
            mid_close=42000.0,
            sigma_240=0.015,
            bin_width=10.0,
            va=invalid_va,
            order_flow=OrderFlowMetrics(0, 0, 0, 0, 0, 0, 0),
            qimb_close=0,
            qimb_ema=0,
            spread_avg_60m=1.0,
        )
        signal = engine.process(features)
        assert signal.action == Action.HOLD
        assert "Invalid VA" in signal.reason

    def test_reset(self, sample_config):
        engine = SignalEngine(sample_config)
        engine.acceptance.consecutive_above_vah = 5
        engine.last_signal_ts = 1000
        engine.reset()
        assert engine.acceptance.consecutive_above_vah == 0
        assert engine.last_signal_ts is None


class TestAcceptanceTracking:
    """Tests for acceptance state tracking."""

    def test_acceptance_above_vah(self, sample_config):
        engine = SignalEngine(sample_config)
        va = ValueArea(
            poc=42000.0, vah=42200.0, val=41800.0,
            coverage=0.7, bin_count=20, total_volume=1000.0,
            bin_width=10.0, is_valid=True,
        )

        # Simulate consecutive closes above VAH
        for i in range(5):
            features = Features1m(
                ts_min=1000 + i * 60_000,
                mid_close=42300.0,  # Above VAH
                sigma_240=0.015,
                bin_width=10.0,
                va=va,
                order_flow=OrderFlowMetrics(0, 0, 100, 50, 50, 0, 0),
                qimb_close=0,
                qimb_ema=0,
                spread_avg_60m=1.0,
            )
            engine.process(features)

        assert engine.acceptance.consecutive_above_vah >= 5

    def test_acceptance_resets_on_return_to_va(self, sample_config):
        engine = SignalEngine(sample_config)
        va = ValueArea(
            poc=42000.0, vah=42200.0, val=41800.0,
            coverage=0.7, bin_count=20, total_volume=1000.0,
            bin_width=10.0, is_valid=True,
        )

        # First, go above VAH
        features_above = Features1m(
            ts_min=1000,
            mid_close=42300.0,
            sigma_240=0.015,
            bin_width=10.0,
            va=va,
            order_flow=OrderFlowMetrics(0, 0, 100, 50, 50, 0, 0),
            qimb_close=0,
            qimb_ema=0,
            spread_avg_60m=1.0,
        )
        engine.process(features_above)
        assert engine.acceptance.consecutive_above_vah == 1

        # Return to inside VA
        features_inside = Features1m(
            ts_min=2000,
            mid_close=42000.0,  # Inside VA
            sigma_240=0.015,
            bin_width=10.0,
            va=va,
            order_flow=OrderFlowMetrics(0, 0, 100, 50, 50, 0, 0),
            qimb_close=0,
            qimb_ema=0,
            spread_avg_60m=1.0,
        )
        engine.process(features_inside)
        assert engine.acceptance.consecutive_above_vah == 0


class TestSignalPriority:
    """Tests for signal priority resolution."""

    def test_signal_type_priority_order(self):
        # Verify priority: Break-in > Failed > Breakout
        assert SignalType.BREAKIN_LONG.priority < SignalType.FAILED_BREAKOUT_LONG.priority
        assert SignalType.FAILED_BREAKOUT_LONG.priority < SignalType.BREAKOUT_LONG.priority

        assert SignalType.BREAKIN_SHORT.priority < SignalType.FAILED_BREAKOUT_SHORT.priority
        assert SignalType.FAILED_BREAKOUT_SHORT.priority < SignalType.BREAKOUT_SHORT.priority


class TestCooldown:
    """Tests for signal cooldown."""

    def test_cooldown_blocks_signals(self, sample_config):
        engine = SignalEngine(sample_config)
        engine.last_signal_ts = 1000

        # Should be in cooldown if current time is within cooldown period
        cooldown_minutes = sample_config.risk.cooldown_minutes
        current_ts = 1000 + (cooldown_minutes - 1) * 60_000
        assert engine._in_cooldown(current_ts) is True

    def test_cooldown_expires(self, sample_config):
        engine = SignalEngine(sample_config)
        engine.last_signal_ts = 1000

        # Should not be in cooldown after cooldown period
        cooldown_minutes = sample_config.risk.cooldown_minutes
        current_ts = 1000 + (cooldown_minutes + 1) * 60_000
        assert engine._in_cooldown(current_ts) is False

    def test_no_cooldown_initially(self, sample_config):
        engine = SignalEngine(sample_config)
        assert engine._in_cooldown(1000) is False
