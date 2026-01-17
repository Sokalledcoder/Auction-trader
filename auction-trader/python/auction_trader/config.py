"""Configuration management for the auction-trader system."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import yaml


@dataclass
class InstrumentConfig:
    """Instrument-specific configuration."""
    symbol: str = "BTCUSDT"
    exchange: str = "bybit"
    timeframe: str = "1m"
    tick_size: float = 0.1
    rolling_window_minutes: int = 240


@dataclass
class ValueAreaConfig:
    """Value Area computation configuration."""
    va_fraction: float = 0.70
    base_bin_ticks: int = 1
    alpha_bin: float = 0.25
    bin_width_max_ticks: int = 200
    rebucket_interval_minutes: int = 15
    rebucket_change_pct: float = 0.25
    min_va_bins: int = 20


@dataclass
class OrderFlowConfig:
    """Order flow configuration."""
    max_quote_staleness_ms: int = 250
    ambiguous_trade_frac_max: float = 0.35
    use_tick_rule_fallback: bool = True
    use_qimb: bool = True
    qimb_entry_min: float = 0.10
    qimb_breakout_min: float = 0.10
    qimb_fail_max: float = -0.10
    spread_lookback_minutes: int = 60


@dataclass
class SignalConfig:
    """Signal detection configuration."""
    of_entry_min: float = 0.0
    of_entry_min_norm: float = 0.1
    of_breakout_min: float = 0.0
    of_breakout_min_norm: float = 0.1
    of_fail_max: float = 0.0
    of_fail_max_norm: float = -0.1
    accept_outside_k: int = 3
    enable_retest_mode: bool = True
    enable_flip_on_signal: bool = True


@dataclass
class SizingConfig:
    """Position sizing configuration."""
    risk_pct: float = 0.02
    max_leverage: float = 10.0
    tp1_pct: float = 0.30
    tp2_pct: float = 0.70
    move_stop_to_breakeven_after_tp1: bool = True


@dataclass
class RiskConfig:
    """Risk management configuration."""
    max_hold_minutes: int = 60
    extend_if_profitable: bool = True
    cooldown_minutes: int = 3
    stop_buffer_ticks: int = 2
    max_daily_loss: Optional[float] = None


@dataclass
class ExecutionConfig:
    """Execution configuration."""
    use_limit_for_entry: bool = True
    limit_order_timeout_minutes: int = 1
    slippage_ticks_entry: int = 1
    slippage_ticks_exit: int = 1
    taker_fee_bps: float = 5.0
    maker_fee_bps: float = -1.0


@dataclass
class BacktestConfig:
    """Backtest configuration."""
    funding_rate_8h_bps: float = 1.0
    initial_capital: float = 10000.0
    workers: int = 0  # 0 = auto (CPU/2)


@dataclass
class DatabaseConfig:
    """Database configuration."""
    data_dir: str = "./data"
    raw_db: str = "raw.duckdb"
    features_db: str = "features.duckdb"
    signals_db: str = "signals.db"
    execution_db: str = "execution.db"


@dataclass
class Config:
    """Main configuration for the trading system."""
    instrument: InstrumentConfig = field(default_factory=InstrumentConfig)
    value_area: ValueAreaConfig = field(default_factory=ValueAreaConfig)
    order_flow: OrderFlowConfig = field(default_factory=OrderFlowConfig)
    signal: SignalConfig = field(default_factory=SignalConfig)
    sizing: SizingConfig = field(default_factory=SizingConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        """Create config from dictionary."""
        config = cls()
        section_mapping = {
            "instrument": config.instrument,
            "value_area": config.value_area,
            "order_flow": config.order_flow,
            "signal": config.signal,
            "sizing": config.sizing,
            "risk": config.risk,
            "execution": config.execution,
            "backtest": config.backtest,
            "database": config.database,
        }
        for section_name, section_obj in section_mapping.items():
            if section_name in data:
                for key, value in data[section_name].items():
                    if hasattr(section_obj, key):
                        setattr(section_obj, key, value)
        return config

    def to_dict(self) -> dict:
        """Convert config to dictionary."""
        return {
            "instrument": self.instrument.__dict__.copy(),
            "value_area": self.value_area.__dict__.copy(),
            "order_flow": self.order_flow.__dict__.copy(),
            "signal": self.signal.__dict__.copy(),
            "sizing": self.sizing.__dict__.copy(),
            "risk": self.risk.__dict__.copy(),
            "execution": self.execution.__dict__.copy(),
            "backtest": self.backtest.__dict__.copy(),
            "database": self.database.__dict__.copy(),
        }


def load_config(config_path: Optional[str] = None) -> Config:
    """Load configuration from YAML file.

    Args:
        config_path: Path to config file. If None, looks for:
            1. AUCTION_TRADER_CONFIG env var
            2. ./config/default.yaml
            3. Uses default config

    Returns:
        Config object
    """
    if config_path is None:
        config_path = os.environ.get("AUCTION_TRADER_CONFIG")

    if config_path is None:
        default_path = Path("./config/default.yaml")
        if default_path.exists():
            config_path = str(default_path)

    if config_path is not None:
        path = Path(config_path)
        if path.exists():
            with open(path, "r") as f:
                data = yaml.safe_load(f)
                return Config.from_dict(data or {})

    return Config()


def save_config(config: Config, config_path: str) -> None:
    """Save configuration to YAML file."""
    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w") as f:
        yaml.dump(config.to_dict(), f, default_flow_style=False, sort_keys=False)
