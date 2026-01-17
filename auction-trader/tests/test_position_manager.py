"""Tests for the PositionManager."""

import pytest
from auction_trader.config import Config
from auction_trader.models.types import (
    Signal,
    SignalType,
    Action,
    Position,
    PositionSide,
)
from auction_trader.services.position_manager import (
    PositionManager,
    ExitReason,
    TradeRecord,
)


class TestPositionManagerBasic:
    """Basic tests for PositionManager."""

    def test_creation(self, sample_config):
        pm = PositionManager(sample_config, initial_capital=10000.0)
        assert pm.initial_capital == 10000.0
        assert pm.available_margin == 10000.0
        assert pm.position is None

    def test_has_position(self, sample_config):
        pm = PositionManager(sample_config, initial_capital=10000.0)
        assert pm.has_position is False

    def test_reset(self, sample_config):
        pm = PositionManager(sample_config, initial_capital=10000.0)
        pm.available_margin = 5000.0
        pm.daily_pnl = -500.0
        pm.reset()
        assert pm.available_margin == 10000.0
        assert pm.daily_pnl == 0.0


class TestPositionSizing:
    """Tests for position sizing calculations."""

    def test_calculate_size_basic(self, sample_config):
        pm = PositionManager(sample_config, initial_capital=10000.0)
        # Risk 2% = $200, stop distance $100
        # Size = 200 / 100 = 2 BTC
        size = pm._calculate_size(entry_price=42000.0, stop_price=41900.0)
        expected = (10000.0 * 0.02) / 100.0  # 2.0
        assert abs(size - expected) < 0.01

    def test_calculate_size_leverage_limit(self, sample_config):
        pm = PositionManager(sample_config, initial_capital=1000.0)
        # With small capital and tight stop, leverage might exceed max
        size = pm._calculate_size(entry_price=42000.0, stop_price=41999.0)
        # Should be constrained by max leverage
        max_notional = 1000.0 * sample_config.sizing.max_leverage
        max_size = max_notional / 42000.0
        assert size <= max_size

    def test_calculate_size_zero_stop_distance(self, sample_config):
        pm = PositionManager(sample_config, initial_capital=10000.0)
        size = pm._calculate_size(entry_price=42000.0, stop_price=42000.0)
        assert size == 0.0


class TestPriceCrossing:
    """Tests for price crossing detection."""

    def test_stop_hit_long(self, sample_config):
        pm = PositionManager(sample_config, initial_capital=10000.0)
        pm.position = Position(
            entry_ts=1000,
            side=PositionSide.LONG,
            entry_price=42000.0,
            size=0.1,
            original_size=0.1,
            stop_price=41800.0,
            tp1_price=42100.0,
            tp2_price=42200.0,
        )
        # Low touches stop
        assert pm._check_stop(high=42100.0, low=41750.0) is True
        # Low doesn't reach stop
        assert pm._check_stop(high=42100.0, low=41850.0) is False

    def test_stop_hit_short(self, sample_config):
        pm = PositionManager(sample_config, initial_capital=10000.0)
        pm.position = Position(
            entry_ts=1000,
            side=PositionSide.SHORT,
            entry_price=42000.0,
            size=0.1,
            original_size=0.1,
            stop_price=42200.0,
            tp1_price=41900.0,
            tp2_price=41800.0,
        )
        # High touches stop
        assert pm._check_stop(high=42250.0, low=41900.0) is True
        # High doesn't reach stop
        assert pm._check_stop(high=42150.0, low=41900.0) is False

    def test_tp1_hit_long(self, sample_config):
        pm = PositionManager(sample_config, initial_capital=10000.0)
        pm.position = Position(
            entry_ts=1000,
            side=PositionSide.LONG,
            entry_price=42000.0,
            size=0.1,
            original_size=0.1,
            stop_price=41800.0,
            tp1_price=42100.0,
            tp2_price=42200.0,
            tp1_hit=False,
        )
        # High reaches TP1
        assert pm._check_tp1(high=42150.0, low=41950.0) is True

    def test_tp1_not_triggered_twice(self, sample_config):
        pm = PositionManager(sample_config, initial_capital=10000.0)
        pm.position = Position(
            entry_ts=1000,
            side=PositionSide.LONG,
            entry_price=42000.0,
            size=0.1,
            original_size=0.1,
            stop_price=41800.0,
            tp1_price=42100.0,
            tp2_price=42200.0,
            tp1_hit=True,  # Already hit
        )
        # Should not trigger again
        assert pm._check_tp1(high=42150.0, low=41950.0) is False


class TestTimeStop:
    """Tests for time-based exit."""

    def test_time_stop_triggers(self, sample_config):
        pm = PositionManager(sample_config, initial_capital=10000.0)
        pm.position = Position(
            entry_ts=1000,
            side=PositionSide.LONG,
            entry_price=42000.0,
            size=0.1,
            original_size=0.1,
            stop_price=41800.0,
            tp1_price=42100.0,
            tp2_price=42200.0,
        )
        max_hold_ms = sample_config.risk.max_hold_minutes * 60_000
        current_ts = 1000 + max_hold_ms + 1000  # Past max hold
        current_price = 41900.0  # Not profitable
        assert pm._check_time_stop(current_price, current_ts) is True

    def test_time_stop_extends_if_profitable(self, sample_config):
        config = Config()
        config.risk.extend_if_profitable = True
        pm = PositionManager(config, initial_capital=10000.0)
        pm.position = Position(
            entry_ts=1000,
            side=PositionSide.LONG,
            entry_price=42000.0,
            size=0.1,
            original_size=0.1,
            stop_price=41800.0,
            tp1_price=42100.0,
            tp2_price=42200.0,
        )
        max_hold_ms = config.risk.max_hold_minutes * 60_000
        current_ts = 1000 + max_hold_ms + 1000
        current_price = 42500.0  # Profitable
        assert pm._check_time_stop(current_price, current_ts) is False


class TestFeeCalculation:
    """Tests for trading fee calculation."""

    def test_maker_fee(self, sample_config):
        pm = PositionManager(sample_config, initial_capital=10000.0)
        sample_config.execution.use_limit_for_entry = True
        # Maker fee on entry
        notional = 42000.0 * 0.1  # $4200
        fee = pm._calculate_fee(42000.0, 0.1, is_entry=True)
        expected = notional * sample_config.execution.maker_fee_bps / 10_000
        assert abs(fee - expected) < 0.01

    def test_taker_fee(self, sample_config):
        pm = PositionManager(sample_config, initial_capital=10000.0)
        # Taker fee on exit
        notional = 42000.0 * 0.1
        fee = pm._calculate_fee(42000.0, 0.1, is_entry=False)
        expected = notional * sample_config.execution.taker_fee_bps / 10_000
        assert abs(fee - expected) < 0.01


class TestTradeStats:
    """Tests for trade statistics calculation."""

    def test_stats_empty(self, sample_config):
        pm = PositionManager(sample_config, initial_capital=10000.0)
        stats = pm.get_stats()
        assert stats["total_trades"] == 0
        assert stats["win_rate"] == 0.0

    def test_equity_calculation(self, sample_config):
        pm = PositionManager(sample_config, initial_capital=10000.0)
        pm.trades.append(
            TradeRecord(
                entry_ts=1000,
                exit_ts=2000,
                side=PositionSide.LONG,
                entry_price=42000.0,
                exit_price=42100.0,
                size=0.1,
                pnl_gross=10.0,
                pnl_net=8.0,
                fees=2.0,
                funding=0.0,
                exit_reason=ExitReason.TP1,
                strategy_tag="test",
                hold_minutes=10,
            )
        )
        assert pm.total_pnl == 8.0
        assert pm.equity == 10008.0
