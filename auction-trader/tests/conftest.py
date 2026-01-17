"""Pytest configuration and fixtures."""

import sys
from pathlib import Path

import pytest

# Add the python package to the path
PACKAGE_DIR = Path(__file__).parent.parent / "python"
sys.path.insert(0, str(PACKAGE_DIR))

from auction_trader.config import Config, load_config
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
)


@pytest.fixture
def sample_config() -> Config:
    """Create a sample configuration for testing."""
    return Config()


@pytest.fixture
def sample_trade() -> Trade:
    """Create a sample trade."""
    return Trade(
        ts_ms=1704067200000,  # 2024-01-01 00:00:00 UTC
        price=42000.0,
        size=0.1,
    )


@pytest.fixture
def sample_quote() -> Quote:
    """Create a sample quote."""
    return Quote(
        ts_ms=1704067200000,
        bid_px=41999.5,
        bid_sz=1.5,
        ask_px=42000.5,
        ask_sz=2.0,
    )


@pytest.fixture
def sample_bar() -> Bar1m:
    """Create a sample 1-minute bar."""
    return Bar1m(
        ts_min=1704067200000,
        open=42000.0,
        high=42100.0,
        low=41950.0,
        close=42050.0,
        volume=10.5,
        vwap=42025.0,
        trade_count=150,
        bid_px_close=42049.5,
        ask_px_close=42050.5,
        bid_sz_close=1.0,
        ask_sz_close=1.2,
    )


@pytest.fixture
def sample_value_area() -> ValueArea:
    """Create a sample Value Area."""
    return ValueArea(
        poc=42000.0,
        vah=42200.0,
        val=41800.0,
        coverage=0.70,
        bin_count=25,
        total_volume=1000.0,
        bin_width=10.0,
        is_valid=True,
    )


@pytest.fixture
def sample_order_flow() -> OrderFlowMetrics:
    """Create sample order flow metrics."""
    return OrderFlowMetrics(
        of_1m=50.0,
        of_norm_1m=0.5,
        total_volume=100.0,
        buy_volume=75.0,
        sell_volume=25.0,
        ambiguous_volume=0.0,
        ambiguous_frac=0.0,
    )


@pytest.fixture
def sample_features(sample_value_area, sample_order_flow) -> Features1m:
    """Create sample features."""
    return Features1m(
        ts_min=1704067200000,
        mid_close=42050.0,
        sigma_240=0.015,
        bin_width=10.0,
        va=sample_value_area,
        order_flow=sample_order_flow,
        qimb_close=0.1,
        qimb_ema=0.08,
        spread_avg_60m=1.0,
    )


@pytest.fixture
def bar_history() -> list[Bar1m]:
    """Create a list of bars for testing rolling calculations."""
    bars = []
    base_ts = 1704067200000
    base_price = 42000.0

    for i in range(300):
        # Simulate some price movement
        price_offset = (i % 20 - 10) * 5  # Oscillate around base
        close = base_price + price_offset

        bars.append(Bar1m(
            ts_min=base_ts + i * 60_000,
            open=close - 10,
            high=close + 20,
            low=close - 20,
            close=close,
            volume=10.0 + (i % 5),
            vwap=close,
            trade_count=100 + i % 50,
            bid_px_close=close - 0.5,
            ask_px_close=close + 0.5,
            bid_sz_close=1.0,
            ask_sz_close=1.0,
        ))

    return bars
