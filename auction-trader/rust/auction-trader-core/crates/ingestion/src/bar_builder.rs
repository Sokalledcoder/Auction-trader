//! Minute bar building from trades and quotes.
//!
//! Builds 1-minute OHLCV bars with L1 snapshots at close.

use auction_core::{Bar1m, ClassifiedTrade, Quote, TimestampMs, ts_to_minute};
use std::collections::BTreeMap;

/// Builder for 1-minute bars from classified trades and quotes.
pub struct BarBuilder {
    /// Current bars being built, keyed by minute timestamp.
    bars: BTreeMap<TimestampMs, BarInProgress>,
    /// Recent quotes for close snapshot.
    quotes: Vec<Quote>,
    /// Maximum quotes to keep.
    max_quotes: usize,
}

/// A bar that's currently being built.
#[derive(Debug, Clone)]
struct BarInProgress {
    ts_min: TimestampMs,
    open: Option<f64>,
    high: f64,
    low: f64,
    close: f64,
    volume: f64,
    vwap_numerator: f64,
    trade_count: u32,
}

impl BarInProgress {
    fn new(ts_min: TimestampMs) -> Self {
        Self {
            ts_min,
            open: None,
            high: f64::NEG_INFINITY,
            low: f64::INFINITY,
            close: 0.0,
            volume: 0.0,
            vwap_numerator: 0.0,
            trade_count: 0,
        }
    }

    fn add_trade(&mut self, price: f64, size: f64) {
        if self.open.is_none() {
            self.open = Some(price);
        }
        self.high = self.high.max(price);
        self.low = self.low.min(price);
        self.close = price;
        self.volume += size;
        self.vwap_numerator += price * size;
        self.trade_count += 1;
    }

    fn vwap(&self) -> Option<f64> {
        if self.volume > 0.0 {
            Some(self.vwap_numerator / self.volume)
        } else {
            None
        }
    }

    fn to_bar(&self, quote: Option<&Quote>) -> Option<Bar1m> {
        let open = self.open?;

        let (bid_px, ask_px, bid_sz, ask_sz) = quote
            .map(|q| (q.bid_px, q.ask_px, q.bid_sz, q.ask_sz))
            .unwrap_or((0.0, 0.0, 0.0, 0.0));

        Some(Bar1m {
            ts_min: self.ts_min,
            open,
            high: self.high,
            low: self.low,
            close: self.close,
            volume: self.volume,
            vwap: self.vwap(),
            trade_count: self.trade_count,
            bid_px_close: bid_px,
            ask_px_close: ask_px,
            bid_sz_close: bid_sz,
            ask_sz_close: ask_sz,
        })
    }
}

impl BarBuilder {
    /// Create a new bar builder.
    pub fn new() -> Self {
        Self {
            bars: BTreeMap::new(),
            quotes: Vec::with_capacity(10000),
            max_quotes: 100000,
        }
    }

    /// Add a quote.
    pub fn add_quote(&mut self, quote: Quote) {
        if self.quotes.len() >= self.max_quotes {
            // Remove oldest half
            self.quotes.drain(0..self.max_quotes / 2);
        }
        self.quotes.push(quote);
    }

    /// Add a classified trade.
    pub fn add_trade(&mut self, trade: &ClassifiedTrade) {
        let ts_min = ts_to_minute(trade.trade.ts_ms);

        let bar = self.bars.entry(ts_min).or_insert_with(|| BarInProgress::new(ts_min));
        bar.add_trade(trade.trade.price, trade.trade.size);
    }

    /// Add multiple classified trades.
    pub fn add_trades(&mut self, trades: &[ClassifiedTrade]) {
        for trade in trades {
            self.add_trade(trade);
        }
    }

    /// Find the latest quote at or before the given timestamp.
    fn find_quote(&self, ts_ms: TimestampMs) -> Option<&Quote> {
        // Binary search for the latest quote <= ts_ms
        match self.quotes.binary_search_by_key(&ts_ms, |q| q.ts_ms) {
            Ok(i) => Some(&self.quotes[i]),
            Err(i) => {
                if i > 0 {
                    Some(&self.quotes[i - 1])
                } else {
                    None
                }
            }
        }
    }

    /// Finalize and return completed bars older than the given timestamp.
    ///
    /// Bars for minutes that are complete (current time > minute end) are returned
    /// and removed from the builder.
    pub fn finalize_before(&mut self, current_ts_ms: TimestampMs) -> Vec<Bar1m> {
        let current_minute = ts_to_minute(current_ts_ms);
        let mut completed = Vec::new();

        // Find bars that are complete (their minute has passed)
        let keys_to_remove: Vec<TimestampMs> = self.bars
            .keys()
            .filter(|&&ts| ts < current_minute)
            .copied()
            .collect();

        for ts_min in keys_to_remove {
            if let Some(bar_in_progress) = self.bars.remove(&ts_min) {
                // Find quote at minute close (ts_min + 59999)
                let close_ts = ts_min + 59_999;
                let quote = self.find_quote(close_ts);

                if let Some(bar) = bar_in_progress.to_bar(quote) {
                    completed.push(bar);
                }
            }
        }

        // Sort by timestamp
        completed.sort_by_key(|b| b.ts_min);

        completed
    }

    /// Force finalize a specific minute, even if not complete.
    pub fn force_finalize(&mut self, ts_min: TimestampMs) -> Option<Bar1m> {
        let bar_in_progress = self.bars.remove(&ts_min)?;
        let close_ts = ts_min + 59_999;
        let quote = self.find_quote(close_ts);
        bar_in_progress.to_bar(quote)
    }

    /// Get the number of bars currently being built.
    pub fn pending_bar_count(&self) -> usize {
        self.bars.len()
    }

    /// Clear all state.
    pub fn clear(&mut self) {
        self.bars.clear();
        self.quotes.clear();
    }

    /// Prune old quotes to save memory.
    /// Keeps only quotes newer than the given timestamp.
    pub fn prune_quotes(&mut self, keep_after_ts: TimestampMs) {
        self.quotes.retain(|q| q.ts_ms >= keep_after_ts);
    }
}

impl Default for BarBuilder {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use auction_core::{Trade, TradeSide};

    fn make_classified_trade(ts_ms: i64, price: f64, size: f64) -> ClassifiedTrade {
        ClassifiedTrade {
            trade: Trade { ts_ms, price, size },
            side: TradeSide::Buy,
            quote_bid_px: price - 0.5,
            quote_ask_px: price + 0.5,
            quote_staleness_ms: 10,
        }
    }

    fn make_quote(ts_ms: i64, bid: f64, ask: f64) -> Quote {
        Quote {
            ts_ms,
            bid_px: bid,
            bid_sz: 100.0,
            ask_px: ask,
            ask_sz: 100.0,
        }
    }

    #[test]
    fn test_single_trade() {
        let mut builder = BarBuilder::new();

        // Add quote at minute close
        builder.add_quote(make_quote(60_000 + 59_999, 50000.0, 50001.0));

        // Add trade in first minute
        let trade = make_classified_trade(60_000 + 30_000, 50000.5, 0.1);
        builder.add_trade(&trade);

        // Finalize (current time in second minute)
        let bars = builder.finalize_before(120_000 + 1000);

        assert_eq!(bars.len(), 1);
        assert_eq!(bars[0].ts_min, 60_000);
        assert!((bars[0].open - 50000.5).abs() < 1e-10);
        assert!((bars[0].close - 50000.5).abs() < 1e-10);
        assert!((bars[0].volume - 0.1).abs() < 1e-10);
        assert_eq!(bars[0].trade_count, 1);
        assert!((bars[0].bid_px_close - 50000.0).abs() < 1e-10);
    }

    #[test]
    fn test_multiple_trades_same_minute() {
        let mut builder = BarBuilder::new();
        builder.add_quote(make_quote(60_000 + 59_999, 50000.0, 50002.0));

        // Add multiple trades
        builder.add_trade(&make_classified_trade(60_000 + 10_000, 50000.0, 0.1)); // Open
        builder.add_trade(&make_classified_trade(60_000 + 20_000, 50005.0, 0.2)); // High
        builder.add_trade(&make_classified_trade(60_000 + 30_000, 49995.0, 0.1)); // Low
        builder.add_trade(&make_classified_trade(60_000 + 50_000, 50001.0, 0.1)); // Close

        let bars = builder.finalize_before(120_000 + 1000);

        assert_eq!(bars.len(), 1);
        assert!((bars[0].open - 50000.0).abs() < 1e-10);
        assert!((bars[0].high - 50005.0).abs() < 1e-10);
        assert!((bars[0].low - 49995.0).abs() < 1e-10);
        assert!((bars[0].close - 50001.0).abs() < 1e-10);
        assert!((bars[0].volume - 0.5).abs() < 1e-10);
        assert_eq!(bars[0].trade_count, 4);
    }

    #[test]
    fn test_vwap_calculation() {
        let mut builder = BarBuilder::new();
        builder.add_quote(make_quote(60_000 + 59_999, 50000.0, 50002.0));

        // Trade 1: 100 @ 50000
        // Trade 2: 200 @ 50010
        // VWAP = (100*50000 + 200*50010) / 300 = 15002000/300 = 50006.67
        builder.add_trade(&make_classified_trade(60_000 + 10_000, 50000.0, 100.0));
        builder.add_trade(&make_classified_trade(60_000 + 20_000, 50010.0, 200.0));

        let bars = builder.finalize_before(120_000 + 1000);

        assert_eq!(bars.len(), 1);
        let expected_vwap = (100.0 * 50000.0 + 200.0 * 50010.0) / 300.0;
        assert!((bars[0].vwap.unwrap() - expected_vwap).abs() < 1e-6);
    }

    #[test]
    fn test_multiple_minutes() {
        let mut builder = BarBuilder::new();

        // Quotes for both minutes
        builder.add_quote(make_quote(60_000 + 59_999, 50000.0, 50001.0));
        builder.add_quote(make_quote(120_000 + 59_999, 50010.0, 50011.0));

        // Trades in minute 1
        builder.add_trade(&make_classified_trade(60_000 + 30_000, 50000.5, 0.1));

        // Trades in minute 2
        builder.add_trade(&make_classified_trade(120_000 + 30_000, 50010.5, 0.2));

        // Finalize in minute 3
        let bars = builder.finalize_before(180_000 + 1000);

        assert_eq!(bars.len(), 2);
        assert_eq!(bars[0].ts_min, 60_000);
        assert_eq!(bars[1].ts_min, 120_000);
    }

    #[test]
    fn test_incomplete_bar_not_finalized() {
        let mut builder = BarBuilder::new();

        // Add trade in current minute
        builder.add_trade(&make_classified_trade(60_000 + 30_000, 50000.5, 0.1));

        // Try to finalize (still in same minute)
        let bars = builder.finalize_before(60_000 + 45_000);

        // Should not finalize since minute hasn't ended
        assert_eq!(bars.len(), 0);
        assert_eq!(builder.pending_bar_count(), 1);
    }
}
