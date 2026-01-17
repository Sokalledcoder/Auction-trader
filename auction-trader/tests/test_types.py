"""Tests for data types and models."""

import pytest
from auction_trader.models.types import (
    Trade,
    Quote,
    Bar1m,
    ValueArea,
    OrderFlowMetrics,
    Features1m,
    Signal,
    SignalType,
    Action,
    Position,
    PositionSide,
    AcceptanceState,
    ts_to_minute,
    current_ts_ms,
)


class TestTrade:
    """Tests for Trade type."""

    def test_create_trade(self):
        trade = Trade(ts_ms=1000, price=42000.0, size=0.1)
        assert trade.ts_ms == 1000
        assert trade.price == 42000.0
        assert trade.size == 0.1

    def test_trade_from_dict(self):
        data = {"ts_ms": 2000, "price": 43000.0, "size": 0.2}
        trade = Trade(**data)
        assert trade.ts_ms == 2000
        assert trade.price == 43000.0


class TestQuote:
    """Tests for Quote type."""

    def test_create_quote(self):
        quote = Quote(
            ts_ms=1000,
            bid_px=41999.5,
            bid_sz=1.5,
            ask_px=42000.5,
            ask_sz=2.0,
        )
        assert quote.bid_px == 41999.5
        assert quote.ask_px == 42000.5

    def test_quote_mid(self, sample_quote):
        expected_mid = (41999.5 + 42000.5) / 2
        assert sample_quote.mid == expected_mid

    def test_quote_spread(self, sample_quote):
        expected_spread = 42000.5 - 41999.5
        assert sample_quote.spread == expected_spread

    def test_quote_imbalance(self, sample_quote):
        # bid_sz=1.5, ask_sz=2.0
        expected_imb = (1.5 - 2.0) / (1.5 + 2.0)
        assert abs(sample_quote.imbalance - expected_imb) < 1e-10


class TestBar1m:
    """Tests for Bar1m type."""

    def test_create_bar(self, sample_bar):
        assert sample_bar.open == 42000.0
        assert sample_bar.high == 42100.0
        assert sample_bar.low == 41950.0
        assert sample_bar.close == 42050.0

    def test_bar_mid_close(self, sample_bar):
        expected = (42049.5 + 42050.5) / 2
        assert sample_bar.mid_close == expected

    def test_bar_spread_close(self, sample_bar):
        expected = 42050.5 - 42049.5
        assert sample_bar.spread_close == expected

    def test_bar_qimb_close(self, sample_bar):
        # bid_sz_close=1.0, ask_sz_close=1.2
        expected = (1.0 - 1.2) / (1.0 + 1.2)
        assert abs(sample_bar.qimb_close - expected) < 1e-10


class TestValueArea:
    """Tests for ValueArea type."""

    def test_create_value_area(self, sample_value_area):
        assert sample_value_area.poc == 42000.0
        assert sample_value_area.vah == 42200.0
        assert sample_value_area.val == 41800.0
        assert sample_value_area.is_valid is True

    def test_invalid_value_area(self):
        va = ValueArea.invalid()
        assert va.is_valid is False
        assert va.poc == 0.0

    def test_value_area_width(self, sample_value_area):
        # ValueArea doesn't have a width property, calculate it
        expected_width = sample_value_area.vah - sample_value_area.val
        assert expected_width == 400.0


class TestSignalType:
    """Tests for SignalType enum."""

    def test_signal_priorities(self):
        # Break-in should have highest priority (lowest number)
        assert SignalType.BREAKIN_LONG.priority < SignalType.BREAKOUT_LONG.priority
        assert SignalType.BREAKIN_SHORT.priority < SignalType.BREAKOUT_SHORT.priority

        # Failed breakout should be between break-in and breakout
        assert SignalType.FAILED_BREAKOUT_LONG.priority < SignalType.BREAKOUT_LONG.priority
        assert SignalType.FAILED_BREAKOUT_LONG.priority > SignalType.BREAKIN_LONG.priority

    def test_is_long(self):
        assert SignalType.BREAKIN_LONG.is_long() is True
        assert SignalType.BREAKOUT_LONG.is_long() is True
        assert SignalType.FAILED_BREAKOUT_LONG.is_long() is True
        assert SignalType.BREAKIN_SHORT.is_long() is False
        assert SignalType.BREAKOUT_SHORT.is_long() is False


class TestPosition:
    """Tests for Position type."""

    def test_create_position(self):
        pos = Position(
            entry_ts=1000,
            side=PositionSide.LONG,
            entry_price=42000.0,
            size=0.1,
            original_size=0.1,
            stop_price=41800.0,
            tp1_price=42100.0,
            tp2_price=42200.0,
        )
        assert pos.side == PositionSide.LONG
        assert pos.entry_price == 42000.0

    def test_position_profitable_long(self):
        pos = Position(
            entry_ts=1000,
            side=PositionSide.LONG,
            entry_price=42000.0,
            size=0.1,
            original_size=0.1,
            stop_price=41800.0,
            tp1_price=42100.0,
            tp2_price=42200.0,
        )
        # Test profitable check (current price > entry for long)
        assert pos.is_profitable(42100.0) is True
        assert pos.is_profitable(41900.0) is False

    def test_position_profitable_short(self):
        pos = Position(
            entry_ts=1000,
            side=PositionSide.SHORT,
            entry_price=42000.0,
            size=0.1,
            original_size=0.1,
            stop_price=42200.0,
            tp1_price=41900.0,
            tp2_price=41800.0,
        )
        # Test profitable check (current price < entry for short)
        assert pos.is_profitable(41900.0) is True
        assert pos.is_profitable(42100.0) is False


class TestAcceptanceState:
    """Tests for AcceptanceState."""

    def test_initial_state(self):
        state = AcceptanceState()
        assert state.consecutive_above_vah == 0
        assert state.consecutive_below_val == 0

    def test_reset_above(self):
        state = AcceptanceState()
        state.consecutive_above_vah = 5
        state.locked_vah = 42200.0
        state.reset_above()
        assert state.consecutive_above_vah == 0
        assert state.locked_vah is None


class TestUtilityFunctions:
    """Tests for utility functions."""

    def test_ts_to_minute(self):
        # 1704067200000 ms = 2024-01-01 00:00:00 UTC
        ts_ms = 1704067200000
        minute_ts = ts_to_minute(ts_ms)
        assert minute_ts == ts_ms  # Already at minute boundary

        # Test with milliseconds within the minute
        ts_ms_offset = 1704067200000 + 30_000  # 30 seconds in
        minute_ts = ts_to_minute(ts_ms_offset)
        assert minute_ts == 1704067200000

    def test_current_ts_ms(self):
        ts = current_ts_ms()
        # Should be a reasonable timestamp (after 2024)
        assert ts > 1704067200000
