//! Rolling volume-at-price histogram.
//!
//! Maintains a rolling histogram of volume by price bin over a configurable window.

use ordered_float::OrderedFloat;
use std::collections::{BTreeMap, VecDeque};

/// Volume data for a single minute.
#[derive(Debug, Clone)]
pub struct MinuteVolume {
    /// Timestamp (minute boundary).
    pub ts_min: i64,
    /// Volume by base bin.
    pub bins: BTreeMap<OrderedFloat<f64>, f64>,
}

/// Rolling histogram for volume-at-price.
pub struct RollingHistogram {
    /// Base bin width (finest resolution, typically tick_size).
    base_bin: f64,
    /// Rolling window in minutes.
    window: usize,
    /// Per-minute volume snapshots.
    minute_volumes: VecDeque<MinuteVolume>,
    /// Aggregated histogram at base resolution.
    aggregated: BTreeMap<OrderedFloat<f64>, f64>,
    /// Current minute being accumulated.
    current_minute: Option<i64>,
    /// Current minute's bins.
    current_bins: BTreeMap<OrderedFloat<f64>, f64>,
}

impl RollingHistogram {
    /// Create a new rolling histogram.
    pub fn new(base_bin: f64, window: usize) -> Self {
        Self {
            base_bin,
            window,
            minute_volumes: VecDeque::with_capacity(window),
            aggregated: BTreeMap::new(),
            current_minute: None,
            current_bins: BTreeMap::new(),
        }
    }

    /// Get the bin key for a price.
    fn bin_key(&self, price: f64) -> OrderedFloat<f64> {
        let bin = (price / self.base_bin).floor() * self.base_bin;
        OrderedFloat(bin)
    }

    /// Add a trade.
    pub fn add_trade(&mut self, ts_min: i64, price: f64, size: f64) {
        // Check if we need to finalize current minute
        if let Some(current) = self.current_minute {
            if ts_min != current {
                self.finalize_minute(current);
            }
        }

        self.current_minute = Some(ts_min);

        let key = self.bin_key(price);
        *self.current_bins.entry(key).or_insert(0.0) += size;
    }

    /// Finalize the current minute and add to rolling window.
    fn finalize_minute(&mut self, ts_min: i64) {
        if self.current_bins.is_empty() {
            return;
        }

        // Add to aggregated histogram
        for (&key, &vol) in &self.current_bins {
            *self.aggregated.entry(key).or_insert(0.0) += vol;
        }

        // Store minute snapshot
        self.minute_volumes.push_back(MinuteVolume {
            ts_min,
            bins: std::mem::take(&mut self.current_bins),
        });

        // Remove old minutes if window exceeded
        while self.minute_volumes.len() > self.window {
            if let Some(old) = self.minute_volumes.pop_front() {
                // Subtract from aggregated
                for (key, vol) in old.bins {
                    if let Some(agg_vol) = self.aggregated.get_mut(&key) {
                        *agg_vol -= vol;
                        if *agg_vol <= 1e-10 {
                            self.aggregated.remove(&key);
                        }
                    }
                }
            }
        }
    }

    /// Force finalize current minute (call at minute boundary).
    pub fn flush_current_minute(&mut self) {
        if let Some(ts) = self.current_minute.take() {
            self.finalize_minute(ts);
        }
    }

    /// Get the aggregated histogram at base resolution.
    pub fn histogram(&self) -> &BTreeMap<OrderedFloat<f64>, f64> {
        &self.aggregated
    }

    /// Aggregate to a wider bin width.
    ///
    /// Returns a new histogram with bins at the specified width.
    pub fn aggregate_to(&self, bin_width: f64) -> BTreeMap<OrderedFloat<f64>, f64> {
        let mut result = BTreeMap::new();

        for (&base_key, &vol) in &self.aggregated {
            let agg_key = (base_key.0 / bin_width).floor() * bin_width;
            *result.entry(OrderedFloat(agg_key)).or_insert(0.0) += vol;
        }

        result
    }

    /// Get total volume in the histogram.
    pub fn total_volume(&self) -> f64 {
        self.aggregated.values().sum()
    }

    /// Get number of bins with volume.
    pub fn bin_count(&self) -> usize {
        self.aggregated.len()
    }

    /// Get number of minutes in the window.
    pub fn minute_count(&self) -> usize {
        self.minute_volumes.len()
    }

    /// Check if histogram has enough data.
    pub fn is_ready(&self) -> bool {
        self.minute_volumes.len() >= self.window
    }

    /// Clear all data.
    pub fn clear(&mut self) {
        self.minute_volumes.clear();
        self.aggregated.clear();
        self.current_minute = None;
        self.current_bins.clear();
    }

    /// Rebuild the histogram from stored minute data.
    ///
    /// Useful after changing bin width.
    pub fn rebuild(&mut self) {
        self.aggregated.clear();

        for minute in &self.minute_volumes {
            for (&key, &vol) in &minute.bins {
                *self.aggregated.entry(key).or_insert(0.0) += vol;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_single_trade() {
        let mut hist = RollingHistogram::new(1.0, 5);

        hist.add_trade(0, 100.5, 10.0);
        hist.flush_current_minute();

        assert_eq!(hist.bin_count(), 1);
        assert!((hist.total_volume() - 10.0).abs() < 1e-10);
    }

    #[test]
    fn test_multiple_trades_same_bin() {
        let mut hist = RollingHistogram::new(1.0, 5);

        hist.add_trade(0, 100.2, 5.0);
        hist.add_trade(0, 100.8, 5.0);
        hist.flush_current_minute();

        assert_eq!(hist.bin_count(), 1);
        assert!((hist.total_volume() - 10.0).abs() < 1e-10);
    }

    #[test]
    fn test_multiple_bins() {
        let mut hist = RollingHistogram::new(1.0, 5);

        hist.add_trade(0, 100.5, 5.0);
        hist.add_trade(0, 101.5, 5.0);
        hist.add_trade(0, 102.5, 5.0);
        hist.flush_current_minute();

        assert_eq!(hist.bin_count(), 3);
        assert!((hist.total_volume() - 15.0).abs() < 1e-10);
    }

    #[test]
    fn test_rolling_window() {
        let mut hist = RollingHistogram::new(1.0, 3);

        // Add 5 minutes of data
        for min in 0..5 {
            hist.add_trade(min, 100.0 + min as f64, 10.0);
            hist.flush_current_minute();
        }

        // Should only have 3 minutes
        assert_eq!(hist.minute_count(), 3);
        // Volume from minutes 2, 3, 4
        assert!((hist.total_volume() - 30.0).abs() < 1e-10);
    }

    #[test]
    fn test_aggregate_to_wider_bins() {
        let mut hist = RollingHistogram::new(1.0, 5);

        // Add trades at bins 100, 101, 102, 103
        hist.add_trade(0, 100.5, 10.0);
        hist.add_trade(0, 101.5, 20.0);
        hist.add_trade(0, 102.5, 30.0);
        hist.add_trade(0, 103.5, 40.0);
        hist.flush_current_minute();

        // Aggregate to width 2.0
        let agg = hist.aggregate_to(2.0);

        // Should have 2 bins: 100 (10+20) and 102 (30+40)
        assert_eq!(agg.len(), 2);
        assert!((agg[&OrderedFloat(100.0)] - 30.0).abs() < 1e-10);
        assert!((agg[&OrderedFloat(102.0)] - 70.0).abs() < 1e-10);
    }

    #[test]
    fn test_is_ready() {
        let mut hist = RollingHistogram::new(1.0, 3);

        assert!(!hist.is_ready());

        for min in 0..3 {
            hist.add_trade(min, 100.0, 10.0);
            hist.flush_current_minute();
        }

        assert!(hist.is_ready());
    }
}
