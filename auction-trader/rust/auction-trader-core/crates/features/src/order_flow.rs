//! Order flow aggregation.
//!
//! Aggregates classified trades into per-minute order flow metrics.

use auction_core::{ClassifiedTrade, OrderFlowMetrics, TradeSide, TimestampMs, ts_to_minute};
use std::collections::BTreeMap;

/// Accumulator for order flow within a minute.
#[derive(Debug, Clone, Default)]
struct MinuteAccumulator {
    buy_volume: f64,
    sell_volume: f64,
    ambiguous_volume: f64,
}

impl MinuteAccumulator {
    fn add(&mut self, trade: &ClassifiedTrade) {
        let size = trade.trade.size;
        match trade.side {
            TradeSide::Buy => self.buy_volume += size,
            TradeSide::Sell => self.sell_volume += size,
            TradeSide::Ambiguous => self.ambiguous_volume += size,
        }
    }

    fn to_metrics(&self) -> OrderFlowMetrics {
        let total_volume = self.buy_volume + self.sell_volume + self.ambiguous_volume;
        let of_1m = self.buy_volume - self.sell_volume;
        let of_norm_1m = if total_volume > 0.0 {
            of_1m / total_volume
        } else {
            0.0
        };
        let ambiguous_frac = if total_volume > 0.0 {
            self.ambiguous_volume / total_volume
        } else {
            0.0
        };

        OrderFlowMetrics {
            of_1m,
            of_norm_1m,
            total_volume,
            buy_volume: self.buy_volume,
            sell_volume: self.sell_volume,
            ambiguous_volume: self.ambiguous_volume,
            ambiguous_frac,
        }
    }
}

/// Order flow aggregator that tracks per-minute metrics.
pub struct OrderFlowAggregator {
    /// Accumulators by minute.
    minutes: BTreeMap<TimestampMs, MinuteAccumulator>,
    /// Maximum minutes to keep.
    max_minutes: usize,
}

impl OrderFlowAggregator {
    /// Create a new order flow aggregator.
    pub fn new(max_minutes: usize) -> Self {
        Self {
            minutes: BTreeMap::new(),
            max_minutes,
        }
    }

    /// Add a classified trade.
    pub fn add_trade(&mut self, trade: &ClassifiedTrade) {
        let ts_min = ts_to_minute(trade.trade.ts_ms);
        self.minutes
            .entry(ts_min)
            .or_default()
            .add(trade);

        // Prune old minutes
        while self.minutes.len() > self.max_minutes {
            if let Some((&oldest, _)) = self.minutes.iter().next() {
                self.minutes.remove(&oldest);
            }
        }
    }

    /// Add multiple trades.
    pub fn add_trades(&mut self, trades: &[ClassifiedTrade]) {
        for trade in trades {
            self.add_trade(trade);
        }
    }

    /// Get metrics for a specific minute.
    pub fn get_minute(&self, ts_min: TimestampMs) -> Option<OrderFlowMetrics> {
        self.minutes.get(&ts_min).map(|acc| acc.to_metrics())
    }

    /// Get metrics for the most recent minute.
    pub fn get_latest(&self) -> Option<(TimestampMs, OrderFlowMetrics)> {
        self.minutes
            .iter()
            .last()
            .map(|(&ts, acc)| (ts, acc.to_metrics()))
    }

    /// Get rolling metrics over the last N minutes.
    pub fn get_rolling(&self, minutes: usize) -> OrderFlowMetrics {
        let mut total = MinuteAccumulator::default();

        for acc in self.minutes.values().rev().take(minutes) {
            total.buy_volume += acc.buy_volume;
            total.sell_volume += acc.sell_volume;
            total.ambiguous_volume += acc.ambiguous_volume;
        }

        total.to_metrics()
    }

    /// Get the number of minutes tracked.
    pub fn minute_count(&self) -> usize {
        self.minutes.len()
    }

    /// Clear all data.
    pub fn clear(&mut self) {
        self.minutes.clear();
    }
}

/// Quote imbalance tracker.
pub struct QuoteImbalanceTracker {
    /// Recent qimb values for EMA calculation.
    values: Vec<(TimestampMs, f64)>,
    /// Maximum values to keep.
    max_values: usize,
    /// EMA decay factor.
    ema_alpha: f64,
}

impl QuoteImbalanceTracker {
    /// Create a new quote imbalance tracker.
    ///
    /// # Arguments
    /// * `max_values` - Maximum quote updates to keep
    /// * `ema_span_seconds` - EMA span in seconds (for alpha calculation)
    pub fn new(max_values: usize, ema_span_seconds: u32) -> Self {
        // Alpha for EMA: 2 / (span + 1)
        // For span in seconds, assuming ~10 updates per second
        let ema_alpha = 2.0 / (ema_span_seconds as f64 * 10.0 + 1.0);

        Self {
            values: Vec::with_capacity(max_values),
            max_values,
            ema_alpha,
        }
    }

    /// Add a quote imbalance value.
    pub fn add(&mut self, ts_ms: TimestampMs, qimb: f64) {
        if self.values.len() >= self.max_values {
            self.values.remove(0);
        }
        self.values.push((ts_ms, qimb));
    }

    /// Get the latest qimb value.
    pub fn latest(&self) -> Option<f64> {
        self.values.last().map(|(_, v)| *v)
    }

    /// Calculate EMA of qimb values in the given minute.
    pub fn ema_for_minute(&self, ts_min: TimestampMs) -> f64 {
        let minute_end = ts_min + 60_000;

        // Filter to values in this minute
        let minute_values: Vec<f64> = self.values
            .iter()
            .filter(|(ts, _)| *ts >= ts_min && *ts < minute_end)
            .map(|(_, v)| *v)
            .collect();

        if minute_values.is_empty() {
            return 0.0;
        }

        // Calculate EMA
        let mut ema = minute_values[0];
        for &v in &minute_values[1..] {
            ema = self.ema_alpha * v + (1.0 - self.ema_alpha) * ema;
        }

        ema
    }

    /// Get simple average of qimb values in the given minute.
    pub fn avg_for_minute(&self, ts_min: TimestampMs) -> f64 {
        let minute_end = ts_min + 60_000;

        let mut sum = 0.0;
        let mut count = 0;

        for (ts, v) in &self.values {
            if *ts >= ts_min && *ts < minute_end {
                sum += v;
                count += 1;
            }
        }

        if count > 0 {
            sum / count as f64
        } else {
            0.0
        }
    }

    /// Clear all data.
    pub fn clear(&mut self) {
        self.values.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use auction_core::Trade;

    fn make_classified(ts_ms: i64, size: f64, side: TradeSide) -> ClassifiedTrade {
        ClassifiedTrade {
            trade: Trade {
                ts_ms,
                price: 50000.0,
                size,
            },
            side,
            quote_bid_px: 50000.0,
            quote_ask_px: 50001.0,
            quote_staleness_ms: 10,
        }
    }

    #[test]
    fn test_single_minute() {
        let mut agg = OrderFlowAggregator::new(10);

        agg.add_trade(&make_classified(60_000, 1.0, TradeSide::Buy));
        agg.add_trade(&make_classified(60_000 + 30_000, 2.0, TradeSide::Sell));
        agg.add_trade(&make_classified(60_000 + 45_000, 0.5, TradeSide::Ambiguous));

        let metrics = agg.get_minute(60_000).unwrap();

        assert!((metrics.buy_volume - 1.0).abs() < 1e-10);
        assert!((metrics.sell_volume - 2.0).abs() < 1e-10);
        assert!((metrics.ambiguous_volume - 0.5).abs() < 1e-10);
        assert!((metrics.of_1m - (-1.0)).abs() < 1e-10); // 1 - 2 = -1
        assert!((metrics.total_volume - 3.5).abs() < 1e-10);
    }

    #[test]
    fn test_multiple_minutes() {
        let mut agg = OrderFlowAggregator::new(10);

        // Minute 1
        agg.add_trade(&make_classified(60_000, 1.0, TradeSide::Buy));

        // Minute 2
        agg.add_trade(&make_classified(120_000, 2.0, TradeSide::Sell));

        let m1 = agg.get_minute(60_000).unwrap();
        let m2 = agg.get_minute(120_000).unwrap();

        assert!((m1.of_1m - 1.0).abs() < 1e-10);
        assert!((m2.of_1m - (-2.0)).abs() < 1e-10);
    }

    #[test]
    fn test_rolling_metrics() {
        let mut agg = OrderFlowAggregator::new(10);

        // 3 minutes of data
        agg.add_trade(&make_classified(60_000, 1.0, TradeSide::Buy));
        agg.add_trade(&make_classified(120_000, 2.0, TradeSide::Buy));
        agg.add_trade(&make_classified(180_000, 3.0, TradeSide::Sell));

        let rolling = agg.get_rolling(3);

        assert!((rolling.buy_volume - 3.0).abs() < 1e-10);
        assert!((rolling.sell_volume - 3.0).abs() < 1e-10);
        assert!((rolling.of_1m - 0.0).abs() < 1e-10);
    }

    #[test]
    fn test_normalized_of() {
        let mut agg = OrderFlowAggregator::new(10);

        // All buy
        agg.add_trade(&make_classified(60_000, 10.0, TradeSide::Buy));

        let metrics = agg.get_minute(60_000).unwrap();
        assert!((metrics.of_norm_1m - 1.0).abs() < 1e-10);

        // All sell in next minute
        agg.add_trade(&make_classified(120_000, 10.0, TradeSide::Sell));

        let metrics2 = agg.get_minute(120_000).unwrap();
        assert!((metrics2.of_norm_1m - (-1.0)).abs() < 1e-10);
    }

    #[test]
    fn test_qimb_tracker() {
        let mut tracker = QuoteImbalanceTracker::new(1000, 60);

        // Add some values
        tracker.add(60_000, 0.1);
        tracker.add(60_500, 0.2);
        tracker.add(61_000, 0.3);

        let avg = tracker.avg_for_minute(60_000);
        assert!((avg - 0.2).abs() < 1e-10); // (0.1 + 0.2 + 0.3) / 3 = 0.2
    }
}
