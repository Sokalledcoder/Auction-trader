//! Configuration structures for the auction-trader system.

use serde::{Deserialize, Serialize};

/// Main configuration for the trading system.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Config {
    /// Instrument configuration.
    pub instrument: InstrumentConfig,
    /// Value Area configuration.
    pub value_area: ValueAreaConfig,
    /// Order flow / initiative configuration.
    pub order_flow: OrderFlowConfig,
    /// Signal configuration.
    pub signal: SignalConfig,
    /// Position sizing configuration.
    pub sizing: SizingConfig,
    /// Risk management configuration.
    pub risk: RiskConfig,
    /// Execution configuration.
    pub execution: ExecutionConfig,
    /// Backtest configuration.
    pub backtest: BacktestConfig,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            instrument: InstrumentConfig::default(),
            value_area: ValueAreaConfig::default(),
            order_flow: OrderFlowConfig::default(),
            signal: SignalConfig::default(),
            sizing: SizingConfig::default(),
            risk: RiskConfig::default(),
            execution: ExecutionConfig::default(),
            backtest: BacktestConfig::default(),
        }
    }
}

/// Instrument-specific configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InstrumentConfig {
    /// Trading symbol (e.g., "BTCUSDT").
    pub symbol: String,
    /// Exchange name.
    pub exchange: String,
    /// Timeframe.
    pub timeframe: String,
    /// Tick size (minimum price increment).
    pub tick_size: f64,
    /// Rolling window in minutes.
    pub rolling_window_minutes: u32,
}

impl Default for InstrumentConfig {
    fn default() -> Self {
        Self {
            symbol: "BTCUSDT".to_string(),
            exchange: "bybit".to_string(),
            timeframe: "1m".to_string(),
            tick_size: 0.1,
            rolling_window_minutes: 240,
        }
    }
}

/// Value Area computation configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ValueAreaConfig {
    /// Target VA coverage (e.g., 0.70 for 70%).
    pub va_fraction: f64,
    /// Base bin width in ticks.
    pub base_bin_ticks: u32,
    /// Alpha for volatility-scaled bin width.
    pub alpha_bin: f64,
    /// Maximum bin width in ticks.
    pub bin_width_max_ticks: u32,
    /// Rebucket interval in minutes.
    pub rebucket_interval_minutes: u32,
    /// Rebucket change percentage threshold.
    pub rebucket_change_pct: f64,
    /// Minimum number of bins for valid VA.
    pub min_va_bins: u32,
}

impl Default for ValueAreaConfig {
    fn default() -> Self {
        Self {
            va_fraction: 0.70,
            base_bin_ticks: 1,
            alpha_bin: 0.25,
            bin_width_max_ticks: 200,
            rebucket_interval_minutes: 15,
            rebucket_change_pct: 0.25,
            min_va_bins: 20,
        }
    }
}

/// Order flow configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrderFlowConfig {
    /// Maximum quote staleness for trade classification (ms).
    pub max_quote_staleness_ms: i64,
    /// Maximum ambiguous trade fraction before requiring dual confirmation.
    pub ambiguous_trade_frac_max: f64,
    /// Whether to use tick rule fallback for ambiguous trades.
    pub use_tick_rule_fallback: bool,
    /// Whether to use quote imbalance as a signal filter.
    pub use_qimb: bool,
    /// Minimum qimb for entry signals.
    pub qimb_entry_min: f64,
    /// Minimum qimb for breakout signals.
    pub qimb_breakout_min: f64,
    /// Maximum qimb for failed breakout signals (should be negative).
    pub qimb_fail_max: f64,
    /// Lookback for spread average (minutes).
    pub spread_lookback_minutes: u32,
}

impl Default for OrderFlowConfig {
    fn default() -> Self {
        Self {
            max_quote_staleness_ms: 250,
            ambiguous_trade_frac_max: 0.35,
            use_tick_rule_fallback: true,
            use_qimb: true,
            qimb_entry_min: 0.10,
            qimb_breakout_min: 0.10,
            qimb_fail_max: -0.10,
            spread_lookback_minutes: 60,
        }
    }
}

/// Signal detection configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SignalConfig {
    /// Minimum absolute OF for entry.
    pub of_entry_min: f64,
    /// Minimum normalized OF for entry.
    pub of_entry_min_norm: f64,
    /// Minimum absolute OF for breakout.
    pub of_breakout_min: f64,
    /// Minimum normalized OF for breakout.
    pub of_breakout_min_norm: f64,
    /// Maximum absolute OF for failed breakout (should be <= 0).
    pub of_fail_max: f64,
    /// Maximum normalized OF for failed breakout.
    pub of_fail_max_norm: f64,
    /// Consecutive closes outside VA for acceptance.
    pub accept_outside_k: u32,
    /// Enable retest mode for breakouts.
    pub enable_retest_mode: bool,
    /// Enable flip-on-signal (reverse without explicit exit).
    pub enable_flip_on_signal: bool,
}

impl Default for SignalConfig {
    fn default() -> Self {
        Self {
            of_entry_min: 0.0,
            of_entry_min_norm: 0.1,
            of_breakout_min: 0.0,
            of_breakout_min_norm: 0.1,
            of_fail_max: 0.0,
            of_fail_max_norm: -0.1,
            accept_outside_k: 3,
            enable_retest_mode: true,
            enable_flip_on_signal: true,
        }
    }
}

/// Position sizing configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SizingConfig {
    /// Risk per trade as fraction of available margin.
    pub risk_pct: f64,
    /// Maximum leverage allowed.
    pub max_leverage: f64,
    /// TP1 allocation (fraction of position).
    pub tp1_pct: f64,
    /// TP2 allocation (fraction of position).
    pub tp2_pct: f64,
    /// Move stop to breakeven after TP1.
    pub move_stop_to_breakeven_after_tp1: bool,
}

impl Default for SizingConfig {
    fn default() -> Self {
        Self {
            risk_pct: 0.02,
            max_leverage: 10.0,
            tp1_pct: 0.30,
            tp2_pct: 0.70,
            move_stop_to_breakeven_after_tp1: true,
        }
    }
}

/// Risk management configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RiskConfig {
    /// Maximum hold time in minutes (base).
    pub max_hold_minutes: u32,
    /// Extend hold time if profitable.
    pub extend_if_profitable: bool,
    /// Cooldown period after exit (minutes).
    pub cooldown_minutes: u32,
    /// Stop buffer in ticks.
    pub stop_buffer_ticks: u32,
    /// Maximum daily loss (absolute value).
    pub max_daily_loss: Option<f64>,
}

impl Default for RiskConfig {
    fn default() -> Self {
        Self {
            max_hold_minutes: 60,
            extend_if_profitable: true,
            cooldown_minutes: 3,
            stop_buffer_ticks: 2,
            max_daily_loss: None,
        }
    }
}

/// Execution configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExecutionConfig {
    /// Use limit orders for entry.
    pub use_limit_for_entry: bool,
    /// Limit order timeout before converting to market (minutes).
    pub limit_order_timeout_minutes: u32,
    /// Slippage for entry (ticks).
    pub slippage_ticks_entry: u32,
    /// Slippage for exit (ticks).
    pub slippage_ticks_exit: u32,
    /// Taker fee in basis points.
    pub taker_fee_bps: f64,
    /// Maker fee in basis points (negative = rebate).
    pub maker_fee_bps: f64,
}

impl Default for ExecutionConfig {
    fn default() -> Self {
        Self {
            use_limit_for_entry: true,
            limit_order_timeout_minutes: 1,
            slippage_ticks_entry: 1,
            slippage_ticks_exit: 1,
            taker_fee_bps: 5.0,
            maker_fee_bps: -1.0,
        }
    }
}

/// Backtest configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BacktestConfig {
    /// Funding rate per 8h in basis points.
    pub funding_rate_8h_bps: f64,
    /// Initial capital for backtesting.
    pub initial_capital: f64,
    /// Number of parallel workers (0 = auto).
    pub workers: u32,
}

impl Default for BacktestConfig {
    fn default() -> Self {
        Self {
            funding_rate_8h_bps: 1.0,
            initial_capital: 10000.0,
            workers: 0,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_config() {
        let config = Config::default();
        assert_eq!(config.value_area.va_fraction, 0.70);
        assert_eq!(config.signal.accept_outside_k, 3);
        assert_eq!(config.sizing.risk_pct, 0.02);
    }
}
