//! Feature computation engine.
//!
//! Combines all feature components into a unified interface.

use auction_core::{
    Bar1m, ClassifiedTrade, Config, Features1m, Quote, TimestampMs, ValueArea,
    ts_to_minute,
};
use crate::{
    histogram::RollingHistogram,
    order_flow::{OrderFlowAggregator, QuoteImbalanceTracker},
    value_area::{ValueAreaComputer, ValueAreaConfig},
    volatility::RollingVolatility,
};
use std::collections::VecDeque;

/// Feature computation engine.
pub struct FeatureEngine {
    /// Rolling volatility calculator.
    volatility: RollingVolatility,
    /// Rolling volume histogram.
    histogram: RollingHistogram,
    /// Value Area computer.
    va_computer: ValueAreaComputer,
    /// Order flow aggregator.
    order_flow: OrderFlowAggregator,
    /// Quote imbalance tracker.
    qimb_tracker: QuoteImbalanceTracker,
    /// Rolling spread tracker (for 60-min average).
    spreads: VecDeque<(TimestampMs, f64)>,
    /// Configuration.
    tick_size: f64,
    alpha_bin: f64,
    bin_width_max: f64,
    spread_lookback: usize,
    rolling_window: usize,
    /// Current bin width.
    current_bin_width: f64,
    /// Last rebucket minute.
    last_rebucket_min: Option<TimestampMs>,
    rebucket_interval: u32,
    rebucket_change_pct: f64,
}

impl FeatureEngine {
    /// Create a new feature engine from configuration.
    pub fn new(config: &Config) -> Self {
        let rolling_window = config.instrument.rolling_window_minutes as usize;
        let tick_size = config.instrument.tick_size;

        Self {
            volatility: RollingVolatility::new(rolling_window),
            histogram: RollingHistogram::new(tick_size, rolling_window),
            va_computer: ValueAreaComputer::new(ValueAreaConfig {
                va_fraction: config.value_area.va_fraction,
                min_bins: config.value_area.min_va_bins,
            }),
            order_flow: OrderFlowAggregator::new(rolling_window),
            qimb_tracker: QuoteImbalanceTracker::new(
                rolling_window * 1000, // ~1000 updates per minute max
                config.order_flow.spread_lookback_minutes,
            ),
            spreads: VecDeque::with_capacity(config.order_flow.spread_lookback_minutes as usize),
            tick_size,
            alpha_bin: config.value_area.alpha_bin,
            bin_width_max: config.value_area.bin_width_max_ticks as f64 * tick_size,
            spread_lookback: config.order_flow.spread_lookback_minutes as usize,
            rolling_window,
            current_bin_width: tick_size,
            last_rebucket_min: None,
            rebucket_interval: config.value_area.rebucket_interval_minutes,
            rebucket_change_pct: config.value_area.rebucket_change_pct,
        }
    }

    /// Process a quote update.
    pub fn add_quote(&mut self, quote: &Quote) {
        self.qimb_tracker.add(quote.ts_ms, quote.imbalance());
    }

    /// Process a classified trade.
    pub fn add_trade(&mut self, trade: &ClassifiedTrade) {
        let ts_min = ts_to_minute(trade.trade.ts_ms);

        // Add to histogram
        self.histogram.add_trade(ts_min, trade.trade.price, trade.trade.size);

        // Add to order flow
        self.order_flow.add_trade(trade);
    }

    /// Process multiple classified trades.
    pub fn add_trades(&mut self, trades: &[ClassifiedTrade]) {
        for trade in trades {
            self.add_trade(trade);
        }
    }

    /// Process a completed 1-minute bar.
    pub fn add_bar(&mut self, bar: &Bar1m) {
        // Add mid price to volatility
        let mid = bar.mid_close();
        self.volatility.add_price(mid);

        // Track spread
        let spread = bar.spread_close();
        self.spreads.push_back((bar.ts_min, spread));
        while self.spreads.len() > self.spread_lookback {
            self.spreads.pop_front();
        }

        // Flush histogram for this minute
        self.histogram.flush_current_minute();

        // Check if rebucketing needed
        self.maybe_rebucket(bar.ts_min, mid);
    }

    /// Check and perform rebucketing if needed.
    fn maybe_rebucket(&mut self, ts_min: TimestampMs, mid_price: f64) {
        let sigma = self.volatility.volatility().unwrap_or(0.0);

        // Calculate new bin width
        let new_bin_width_raw = self.alpha_bin * mid_price * sigma;
        let new_bin_width = self.round_to_tick(new_bin_width_raw)
            .max(self.tick_size)
            .min(self.bin_width_max);

        // Check if rebucket needed
        let should_rebucket = match self.last_rebucket_min {
            Some(last) => {
                let minutes_since = (ts_min - last) / 60_000;
                let pct_change = if self.current_bin_width > 0.0 {
                    ((new_bin_width - self.current_bin_width) / self.current_bin_width).abs()
                } else {
                    1.0
                };

                minutes_since >= self.rebucket_interval as i64 || pct_change >= self.rebucket_change_pct
            }
            None => true,
        };

        if should_rebucket {
            self.current_bin_width = new_bin_width;
            self.last_rebucket_min = Some(ts_min);
            // Histogram rebuild is implicit - we aggregate on demand
        }
    }

    /// Round a value to the nearest tick.
    fn round_to_tick(&self, value: f64) -> f64 {
        (value / self.tick_size).round() * self.tick_size
    }

    /// Calculate average spread over the lookback period.
    fn avg_spread(&self) -> f64 {
        if self.spreads.is_empty() {
            return self.tick_size;
        }
        let sum: f64 = self.spreads.iter().map(|(_, s)| s).sum();
        sum / self.spreads.len() as f64
    }

    /// Compute features for a specific minute.
    pub fn compute_features(&self, ts_min: TimestampMs, bar: &Bar1m) -> Features1m {
        let mid_close = bar.mid_close();
        let sigma = self.volatility.volatility().unwrap_or(0.0);

        // Compute VA from aggregated histogram
        let agg_hist = self.histogram.aggregate_to(self.current_bin_width);
        let va = self.va_computer.compute(&agg_hist, self.current_bin_width);

        // Get order flow metrics
        let order_flow = self.order_flow
            .get_minute(ts_min)
            .unwrap_or_else(|| auction_core::OrderFlowMetrics {
                of_1m: 0.0,
                of_norm_1m: 0.0,
                total_volume: 0.0,
                buy_volume: 0.0,
                sell_volume: 0.0,
                ambiguous_volume: 0.0,
                ambiguous_frac: 0.0,
            });

        // Get qimb
        let qimb_close = bar.qimb_close();
        let qimb_ema = self.qimb_tracker.ema_for_minute(ts_min);

        Features1m {
            ts_min,
            mid_close,
            sigma_240: sigma,
            bin_width: self.current_bin_width,
            va,
            order_flow,
            qimb_close,
            qimb_ema,
            spread_avg_60m: self.avg_spread(),
        }
    }

    /// Check if the engine has enough warmup data.
    pub fn is_ready(&self) -> bool {
        self.volatility.is_ready() && self.histogram.is_ready()
    }

    /// Get the current rolling window size.
    pub fn window_size(&self) -> usize {
        self.rolling_window
    }

    /// Get the current bin width.
    pub fn current_bin_width(&self) -> f64 {
        self.current_bin_width
    }

    /// Clear all state.
    pub fn clear(&mut self) {
        self.volatility.clear();
        self.histogram.clear();
        self.order_flow.clear();
        self.qimb_tracker.clear();
        self.spreads.clear();
        self.current_bin_width = self.tick_size;
        self.last_rebucket_min = None;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use auction_core::{Trade, TradeSide};

    fn default_config() -> Config {
        let mut config = Config::default();
        config.instrument.rolling_window_minutes = 5; // Small window for testing
        config.value_area.min_va_bins = 3;
        config
    }

    fn make_bar(ts_min: i64, close: f64) -> Bar1m {
        Bar1m {
            ts_min,
            open: close,
            high: close + 10.0,
            low: close - 10.0,
            close,
            volume: 100.0,
            vwap: Some(close),
            trade_count: 10,
            bid_px_close: close - 0.5,
            ask_px_close: close + 0.5,
            bid_sz_close: 100.0,
            ask_sz_close: 100.0,
        }
    }

    fn make_trade(ts_ms: i64, price: f64, size: f64, side: TradeSide) -> ClassifiedTrade {
        ClassifiedTrade {
            trade: Trade { ts_ms, price, size },
            side,
            quote_bid_px: price - 0.5,
            quote_ask_px: price + 0.5,
            quote_staleness_ms: 10,
        }
    }

    #[test]
    fn test_engine_creation() {
        let config = default_config();
        let engine = FeatureEngine::new(&config);
        assert!(!engine.is_ready());
    }

    #[test]
    fn test_warmup() {
        let config = default_config();
        let mut engine = FeatureEngine::new(&config);

        // Add 5 minutes of data
        for i in 0..5 {
            let ts_min = (i + 1) * 60_000;

            // Add trades
            for j in 0..10 {
                let price = 50000.0 + (i * 10 + j) as f64;
                engine.add_trade(&make_trade(ts_min + j * 1000, price, 1.0, TradeSide::Buy));
            }

            // Add bar
            engine.add_bar(&make_bar(ts_min, 50000.0 + i as f64 * 10.0));
        }

        assert!(engine.is_ready());
    }

    #[test]
    fn test_compute_features() {
        let config = default_config();
        let mut engine = FeatureEngine::new(&config);

        // Warm up
        for i in 0..5 {
            let ts_min = (i + 1) * 60_000;

            for j in 0..10 {
                let price = 50000.0 + j as f64;
                engine.add_trade(&make_trade(ts_min + j * 1000, price, 1.0, TradeSide::Buy));
            }

            engine.add_bar(&make_bar(ts_min, 50000.0 + i as f64));
        }

        let ts_min = 5 * 60_000;
        let bar = make_bar(ts_min, 50004.0);
        let features = engine.compute_features(ts_min, &bar);

        assert!(features.va.is_valid || !engine.is_ready());
        assert!(features.sigma_240 >= 0.0);
    }
}
