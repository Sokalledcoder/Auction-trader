//! Trade side inference using bid/ask alignment.
//!
//! Classifies trades as buy-initiated, sell-initiated, or ambiguous based on
//! their price relative to the prevailing bid/ask quote.

use auction_core::{ClassifiedTrade, Quote, Trade, TradeSide};
use std::collections::VecDeque;

/// Statistics about trade classification quality.
#[derive(Debug, Clone, Default)]
pub struct ClassificationStats {
    /// Total trades classified.
    pub total_trades: u64,
    /// Trades classified as buy.
    pub buy_trades: u64,
    /// Trades classified as sell.
    pub sell_trades: u64,
    /// Trades classified as ambiguous.
    pub ambiguous_trades: u64,
    /// Total volume processed.
    pub total_volume: f64,
    /// Buy volume.
    pub buy_volume: f64,
    /// Sell volume.
    pub sell_volume: f64,
    /// Ambiguous volume.
    pub ambiguous_volume: f64,
    /// Sum of quote staleness (ms) for all trades.
    pub total_staleness_ms: i64,
    /// Trades where quote was stale (> max_staleness).
    pub stale_quote_trades: u64,
}

impl ClassificationStats {
    /// Get the fraction of ambiguous volume.
    pub fn ambiguous_frac(&self) -> f64 {
        if self.total_volume > 0.0 {
            self.ambiguous_volume / self.total_volume
        } else {
            0.0
        }
    }

    /// Get the average quote staleness in ms.
    pub fn avg_staleness_ms(&self) -> f64 {
        if self.total_trades > 0 {
            self.total_staleness_ms as f64 / self.total_trades as f64
        } else {
            0.0
        }
    }

    /// Reset statistics.
    pub fn reset(&mut self) {
        *self = Self::default();
    }
}

/// Trade classifier that aligns trades with quotes and infers trade side.
pub struct TradeClassifier {
    /// Maximum allowed quote staleness (ms).
    max_staleness_ms: i64,
    /// Whether to use tick rule fallback for ambiguous trades.
    use_tick_rule: bool,
    /// Recent quotes for alignment.
    quotes: VecDeque<Quote>,
    /// Maximum quotes to keep.
    max_quotes: usize,
    /// Last trade price (for tick rule).
    last_trade_price: Option<f64>,
    /// Last trade side (for zero-tick continuation).
    last_trade_side: TradeSide,
    /// Classification statistics.
    stats: ClassificationStats,
}

impl TradeClassifier {
    /// Create a new trade classifier.
    pub fn new(max_staleness_ms: i64, use_tick_rule: bool) -> Self {
        Self {
            max_staleness_ms,
            use_tick_rule,
            quotes: VecDeque::with_capacity(1000),
            max_quotes: 10000,
            last_trade_price: None,
            last_trade_side: TradeSide::Ambiguous,
            stats: ClassificationStats::default(),
        }
    }

    /// Add a quote to the classifier.
    pub fn add_quote(&mut self, quote: Quote) {
        // Remove quotes older than the new one (quotes should arrive in order)
        while self.quotes.len() >= self.max_quotes {
            self.quotes.pop_front();
        }
        self.quotes.push_back(quote);
    }

    /// Find the latest quote at or before the given timestamp.
    fn find_quote(&self, ts_ms: i64) -> Option<&Quote> {
        // Binary search for the latest quote <= ts_ms
        // Since quotes are in order, we search from the end
        self.quotes
            .iter()
            .rev()
            .find(|q| q.ts_ms <= ts_ms)
    }

    /// Classify a single trade.
    pub fn classify(&mut self, trade: Trade) -> ClassifiedTrade {
        let quote = self.find_quote(trade.ts_ms);

        let (side, quote_bid_px, quote_ask_px, staleness_ms) = match quote {
            Some(q) => {
                let staleness = trade.ts_ms - q.ts_ms;
                let is_stale = staleness > self.max_staleness_ms;

                // Classify based on price vs bid/ask
                let mut side = if trade.price >= q.ask_px {
                    TradeSide::Buy
                } else if trade.price <= q.bid_px {
                    TradeSide::Sell
                } else {
                    TradeSide::Ambiguous
                };

                // Apply tick rule fallback for ambiguous trades
                if side == TradeSide::Ambiguous && self.use_tick_rule {
                    if let Some(last_price) = self.last_trade_price {
                        side = if trade.price > last_price {
                            TradeSide::Buy
                        } else if trade.price < last_price {
                            TradeSide::Sell
                        } else {
                            // Zero-tick continuation
                            self.last_trade_side
                        };
                    }
                }

                // Update stats
                if is_stale {
                    self.stats.stale_quote_trades += 1;
                }

                (side, q.bid_px, q.ask_px, staleness)
            }
            None => {
                // No quote available - use tick rule if enabled
                let side = if self.use_tick_rule {
                    if let Some(last_price) = self.last_trade_price {
                        if trade.price > last_price {
                            TradeSide::Buy
                        } else if trade.price < last_price {
                            TradeSide::Sell
                        } else {
                            self.last_trade_side
                        }
                    } else {
                        TradeSide::Ambiguous
                    }
                } else {
                    TradeSide::Ambiguous
                };
                (side, 0.0, 0.0, i64::MAX)
            }
        };

        // Update statistics
        self.stats.total_trades += 1;
        self.stats.total_volume += trade.size;
        self.stats.total_staleness_ms += staleness_ms.min(self.max_staleness_ms * 10);

        match side {
            TradeSide::Buy => {
                self.stats.buy_trades += 1;
                self.stats.buy_volume += trade.size;
            }
            TradeSide::Sell => {
                self.stats.sell_trades += 1;
                self.stats.sell_volume += trade.size;
            }
            TradeSide::Ambiguous => {
                self.stats.ambiguous_trades += 1;
                self.stats.ambiguous_volume += trade.size;
            }
        }

        // Update last trade info
        self.last_trade_price = Some(trade.price);
        if side != TradeSide::Ambiguous {
            self.last_trade_side = side;
        }

        ClassifiedTrade {
            trade,
            side,
            quote_bid_px,
            quote_ask_px,
            quote_staleness_ms: staleness_ms,
        }
    }

    /// Classify multiple trades, aggregating trades at the same timestamp.
    pub fn classify_batch(&mut self, trades: Vec<Trade>) -> Vec<ClassifiedTrade> {
        if trades.is_empty() {
            return Vec::new();
        }

        // Group trades by timestamp
        let mut result = Vec::with_capacity(trades.len());
        let mut current_ts: Option<i64> = None;
        let mut current_group: Vec<Trade> = Vec::new();

        for trade in trades {
            if current_ts == Some(trade.ts_ms) {
                current_group.push(trade);
            } else {
                // Process previous group
                if !current_group.is_empty() {
                    self.process_trade_group(&mut current_group, &mut result);
                }
                current_ts = Some(trade.ts_ms);
                current_group.clear();
                current_group.push(trade);
            }
        }

        // Process last group
        if !current_group.is_empty() {
            self.process_trade_group(&mut current_group, &mut result);
        }

        result
    }

    /// Process a group of trades at the same timestamp.
    /// Aggregates them into a single classified trade.
    fn process_trade_group(&mut self, group: &mut Vec<Trade>, result: &mut Vec<ClassifiedTrade>) {
        if group.len() == 1 {
            // Single trade - classify normally
            let trade = group.pop().unwrap();
            result.push(self.classify(trade));
        } else {
            // Multiple trades at same timestamp - aggregate
            let ts_ms = group[0].ts_ms;

            // Calculate VWAP and total size
            let mut total_size = 0.0;
            let mut total_value = 0.0;

            for trade in group.iter() {
                total_size += trade.size;
                total_value += trade.price * trade.size;
            }

            let vwap = if total_size > 0.0 {
                total_value / total_size
            } else {
                group[0].price
            };

            // Create aggregated trade
            let aggregated = Trade {
                ts_ms,
                price: vwap,
                size: total_size,
            };

            result.push(self.classify(aggregated));
        }
    }

    /// Get classification statistics.
    pub fn stats(&self) -> &ClassificationStats {
        &self.stats
    }

    /// Reset statistics.
    pub fn reset_stats(&mut self) {
        self.stats.reset();
    }

    /// Clear all state (quotes, statistics, last trade info).
    pub fn clear(&mut self) {
        self.quotes.clear();
        self.last_trade_price = None;
        self.last_trade_side = TradeSide::Ambiguous;
        self.stats.reset();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_quote(ts_ms: i64, bid: f64, ask: f64) -> Quote {
        Quote {
            ts_ms,
            bid_px: bid,
            bid_sz: 1.0,
            ask_px: ask,
            ask_sz: 1.0,
        }
    }

    fn make_trade(ts_ms: i64, price: f64, size: f64) -> Trade {
        Trade { ts_ms, price, size }
    }

    #[test]
    fn test_classify_at_ask() {
        let mut classifier = TradeClassifier::new(250, false);
        classifier.add_quote(make_quote(1000, 50000.0, 50001.0));

        let trade = make_trade(1100, 50001.0, 0.1);
        let classified = classifier.classify(trade);

        assert_eq!(classified.side, TradeSide::Buy);
        assert_eq!(classified.quote_bid_px, 50000.0);
        assert_eq!(classified.quote_ask_px, 50001.0);
    }

    #[test]
    fn test_classify_at_bid() {
        let mut classifier = TradeClassifier::new(250, false);
        classifier.add_quote(make_quote(1000, 50000.0, 50001.0));

        let trade = make_trade(1100, 50000.0, 0.1);
        let classified = classifier.classify(trade);

        assert_eq!(classified.side, TradeSide::Sell);
    }

    #[test]
    fn test_classify_ambiguous() {
        let mut classifier = TradeClassifier::new(250, false);
        classifier.add_quote(make_quote(1000, 50000.0, 50002.0));

        let trade = make_trade(1100, 50001.0, 0.1); // Between bid and ask
        let classified = classifier.classify(trade);

        assert_eq!(classified.side, TradeSide::Ambiguous);
    }

    #[test]
    fn test_tick_rule_fallback() {
        let mut classifier = TradeClassifier::new(250, true);
        classifier.add_quote(make_quote(1000, 50000.0, 50002.0));

        // First trade establishes direction
        let trade1 = make_trade(1100, 50001.0, 0.1); // Ambiguous
        let _ = classifier.classify(trade1);

        // Second trade at higher price
        let trade2 = make_trade(1200, 50001.5, 0.1); // Higher than last
        let classified2 = classifier.classify(trade2);
        assert_eq!(classified2.side, TradeSide::Buy);

        // Third trade at lower price
        let trade3 = make_trade(1300, 50000.5, 0.1); // Lower than last
        let classified3 = classifier.classify(trade3);
        assert_eq!(classified3.side, TradeSide::Sell);
    }

    #[test]
    fn test_zero_tick_continuation() {
        let mut classifier = TradeClassifier::new(250, true);
        classifier.add_quote(make_quote(1000, 50000.0, 50002.0));

        // First trade at ask (buy)
        let trade1 = make_trade(1100, 50002.0, 0.1);
        let classified1 = classifier.classify(trade1);
        assert_eq!(classified1.side, TradeSide::Buy);

        // Second trade at same price (zero-tick)
        classifier.add_quote(make_quote(1150, 50001.0, 50003.0)); // Quote changed
        let trade2 = make_trade(1200, 50002.0, 0.1); // Same price, now ambiguous
        let classified2 = classifier.classify(trade2);
        // Should continue with Buy due to zero-tick rule
        assert_eq!(classified2.side, TradeSide::Buy);
    }

    #[test]
    fn test_batch_aggregation() {
        let mut classifier = TradeClassifier::new(250, false);
        classifier.add_quote(make_quote(1000, 50000.0, 50001.0));

        let trades = vec![
            make_trade(1100, 50001.0, 0.1), // Same timestamp
            make_trade(1100, 50001.0, 0.2), // Same timestamp
            make_trade(1200, 50000.0, 0.1), // Different timestamp
        ];

        let classified = classifier.classify_batch(trades);

        // Should have 2 results: aggregated first two + third
        assert_eq!(classified.len(), 2);
        assert_eq!(classified[0].trade.size, 0.3); // Aggregated
        assert_eq!(classified[1].trade.size, 0.1);
    }

    #[test]
    fn test_stats() {
        let mut classifier = TradeClassifier::new(250, false);
        classifier.add_quote(make_quote(1000, 50000.0, 50001.0));

        classifier.classify(make_trade(1100, 50001.0, 0.1)); // Buy
        classifier.classify(make_trade(1200, 50000.0, 0.2)); // Sell
        classifier.classify(make_trade(1300, 50000.5, 0.3)); // Ambiguous

        let stats = classifier.stats();
        assert_eq!(stats.total_trades, 3);
        assert_eq!(stats.buy_trades, 1);
        assert_eq!(stats.sell_trades, 1);
        assert_eq!(stats.ambiguous_trades, 1);
        assert!((stats.buy_volume - 0.1).abs() < 1e-10);
        assert!((stats.sell_volume - 0.2).abs() < 1e-10);
        assert!((stats.ambiguous_volume - 0.3).abs() < 1e-10);
    }
}
