"""Tests for configuration module."""

import pytest
from pathlib import Path
from auction_trader.config import (
    Config,
    InstrumentConfig,
    ValueAreaConfig,
    SignalConfig,
    SizingConfig,
    RiskConfig,
    ExecutionConfig,
    OrderFlowConfig,
    BacktestConfig,
    DatabaseConfig,
    load_config,
)


class TestDefaultConfig:
    """Tests for default configuration values."""

    def test_default_instrument_config(self):
        config = InstrumentConfig()
        assert config.symbol == "BTCUSDT"
        assert config.exchange == "bybit"
        assert config.timeframe == "1m"
        assert config.tick_size == 0.1
        assert config.rolling_window_minutes == 240

    def test_default_value_area_config(self):
        config = ValueAreaConfig()
        assert config.va_fraction == 0.70
        assert config.min_va_bins == 20

    def test_default_signal_config(self):
        config = SignalConfig()
        assert config.accept_outside_k == 3
        assert config.enable_flip_on_signal is True

    def test_default_sizing_config(self):
        config = SizingConfig()
        assert config.risk_pct == 0.02
        assert config.max_leverage == 10.0
        assert config.tp1_pct == 0.30
        assert config.tp2_pct == 0.70

    def test_default_risk_config(self):
        config = RiskConfig()
        assert config.max_hold_minutes == 60
        assert config.cooldown_minutes == 3  # Actual default

    def test_default_execution_config(self):
        config = ExecutionConfig()
        assert config.use_limit_for_entry is True
        assert config.limit_order_timeout_minutes == 1


class TestConfigFromDict:
    """Tests for Config.from_dict method."""

    def test_partial_dict(self):
        data = {
            "instrument": {
                "symbol": "ETHUSDT",
            },
            "sizing": {
                "risk_pct": 0.01,
            },
        }
        config = Config.from_dict(data)
        assert config.instrument.symbol == "ETHUSDT"
        assert config.sizing.risk_pct == 0.01
        # Defaults should be preserved
        assert config.instrument.exchange == "bybit"
        assert config.sizing.max_leverage == 10.0

    def test_empty_dict(self):
        config = Config.from_dict({})
        # Should use all defaults
        assert config.instrument.symbol == "BTCUSDT"
        assert config.sizing.risk_pct == 0.02

    def test_full_config_dict(self):
        data = {
            "instrument": {
                "symbol": "BTCUSDT",
                "exchange": "bybit",
                "timeframe": "1m",
                "tick_size": 0.1,
                "rolling_window_minutes": 240,
            },
            "value_area": {
                "va_fraction": 0.70,
                "min_va_bins": 20,
                "alpha_bin": 0.10,
                "bin_width_max_ticks": 100,
            },
            "signal": {
                "accept_outside_k": 3,
            },
            "sizing": {
                "risk_pct": 0.02,
                "max_leverage": 10.0,
            },
        }
        config = Config.from_dict(data)
        assert config.value_area.va_fraction == 0.70
        assert config.signal.accept_outside_k == 3


class TestLoadConfig:
    """Tests for load_config function."""

    def test_load_default_config(self, tmp_path):
        # Create a minimal config file
        config_file = tmp_path / "test_config.yaml"
        config_file.write_text("""
instrument:
  symbol: BTCUSDT
  exchange: bybit

sizing:
  risk_pct: 0.015
""")
        config = load_config(str(config_file))
        assert config.instrument.symbol == "BTCUSDT"
        assert config.sizing.risk_pct == 0.015

    def test_load_nonexistent_returns_default(self):
        config = load_config("/nonexistent/path/config.yaml")
        # Should return default config
        assert config.instrument.symbol == "BTCUSDT"

    def test_load_none_returns_default(self):
        config = load_config(None)
        assert config.instrument.symbol == "BTCUSDT"


class TestConfigValidation:
    """Tests for configuration validation."""

    def test_tp_percentages_sum(self):
        config = SizingConfig()
        assert config.tp1_pct + config.tp2_pct == 1.0

    def test_leverage_positive(self):
        config = SizingConfig()
        assert config.max_leverage > 0

    def test_risk_pct_range(self):
        config = SizingConfig()
        assert 0 < config.risk_pct < 1

    def test_va_fraction_range(self):
        config = ValueAreaConfig()
        assert 0 < config.va_fraction < 1
