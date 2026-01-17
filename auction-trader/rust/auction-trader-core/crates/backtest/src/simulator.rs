//! Backtest simulator.
//!
//! Replays historical data and simulates trading based on signals.

use auction_core::{Action, Bar1m, Features1m, Quote, TimestampMs};
use crate::fill_model::{FillModel, FillModelConfig};
use crate::metrics::{BacktestMetrics, MetricsCalculator};
use crate::position::{ClosedTrade, ExitReason, PositionTracker};

/// Backtest configuration.
#[derive(Debug, Clone)]
pub struct BacktestConfig {
    /// Initial capital.
    pub initial_capital: f64,
    /// Fill model configuration.
    pub fill_model: FillModelConfig,
    /// Funding rate per 8h in basis points.
    pub funding_rate_8h_bps: f64,
    /// TP1 allocation (fraction of position).
    pub tp1_pct: f64,
    /// Move stop to breakeven after TP1.
    pub move_stop_to_breakeven: bool,
}

impl Default for BacktestConfig {
    fn default() -> Self {
        Self {
            initial_capital: 10000.0,
            fill_model: FillModelConfig::default(),
            funding_rate_8h_bps: 1.0,
            tp1_pct: 0.30,
            move_stop_to_breakeven: true,
        }
    }
}

/// Trading signal from the signal engine.
#[derive(Debug, Clone)]
pub struct Signal {
    /// Timestamp.
    pub ts_ms: TimestampMs,
    /// Action to take.
    pub action: Action,
    /// Stop price (for entries).
    pub stop_price: Option<f64>,
    /// TP1 price.
    pub tp1_price: Option<f64>,
    /// TP2 price.
    pub tp2_price: Option<f64>,
    /// Position size (contracts).
    pub size: Option<f64>,
    /// Strategy tag.
    pub strategy_tag: String,
}

/// Backtest simulator state.
pub struct BacktestSimulator {
    config: BacktestConfig,
    fill_model: FillModel,
    position_tracker: PositionTracker,
    metrics_calculator: MetricsCalculator,
    /// Current equity.
    equity: f64,
    /// Last funding timestamp.
    last_funding_ts: Option<TimestampMs>,
    /// Funding interval in ms (8 hours).
    funding_interval_ms: i64,
}

impl BacktestSimulator {
    /// Create a new backtest simulator.
    pub fn new(config: BacktestConfig) -> Self {
        let fill_model = FillModel::new(config.fill_model.clone());
        let metrics_calculator = MetricsCalculator::new(config.initial_capital);
        let equity = config.initial_capital;

        Self {
            config,
            fill_model,
            position_tracker: PositionTracker::new(),
            metrics_calculator,
            equity,
            last_funding_ts: None,
            funding_interval_ms: 8 * 60 * 60 * 1000, // 8 hours
        }
    }

    /// Process a signal with the next available quote for fills.
    pub fn process_signal(&mut self, signal: &Signal, quote: &Quote) {
        match signal.action {
            Action::EnterLong => {
                if !self.position_tracker.has_position() {
                    self.enter_long(signal, quote);
                } else if self.position_tracker.is_short() {
                    // Flip: close short, enter long
                    self.close_position(quote.ts_ms, quote, ExitReason::SignalFlip);
                    self.enter_long(signal, quote);
                }
            }
            Action::EnterShort => {
                if !self.position_tracker.has_position() {
                    self.enter_short(signal, quote);
                } else if self.position_tracker.is_long() {
                    // Flip: close long, enter short
                    self.close_position(quote.ts_ms, quote, ExitReason::SignalFlip);
                    self.enter_short(signal, quote);
                }
            }
            Action::Exit => {
                if self.position_tracker.has_position() {
                    self.close_position(quote.ts_ms, quote, ExitReason::Manual);
                }
            }
            Action::Hold => {
                // Do nothing
            }
        }
    }

    /// Enter a long position.
    fn enter_long(&mut self, signal: &Signal, quote: &Quote) {
        let size = signal.size.unwrap_or(0.1);
        let fill = self.fill_model.market_buy(quote.ts_ms, quote, size);

        self.position_tracker.open_position(
            fill,
            signal.stop_price.unwrap_or(0.0),
            signal.tp1_price,
            signal.tp2_price,
            signal.strategy_tag.clone(),
        );
    }

    /// Enter a short position.
    fn enter_short(&mut self, signal: &Signal, quote: &Quote) {
        let size = signal.size.unwrap_or(0.1);
        let fill = self.fill_model.market_sell(quote.ts_ms, quote, size);

        self.position_tracker.open_position(
            fill,
            signal.stop_price.unwrap_or(f64::MAX),
            signal.tp1_price,
            signal.tp2_price,
            signal.strategy_tag.clone(),
        );
    }

    /// Close current position.
    fn close_position(&mut self, ts_ms: TimestampMs, quote: &Quote, reason: ExitReason) {
        if let Some(pos) = &self.position_tracker.position {
            let size = pos.size;
            let exit_price = match pos.side {
                auction_core::PositionSide::Long => {
                    quote.bid_px - self.config.fill_model.slippage_ticks_exit as f64
                        * self.config.fill_model.tick_size
                }
                auction_core::PositionSide::Short => {
                    quote.ask_px + self.config.fill_model.slippage_ticks_exit as f64
                        * self.config.fill_model.tick_size
                }
            };

            let fee = self.fill_model.calculate_fee(exit_price * size, false);
            self.position_tracker.close_position(ts_ms, exit_price, size, fee, reason);
        }
    }

    /// Check and process stops/targets for the current bar.
    pub fn check_stops_targets(&mut self, bar: &Bar1m, quote: &Quote) {
        let position = match &self.position_tracker.position {
            Some(p) => p.clone(),
            None => return,
        };

        // Check stop (worst case assumption: stop hit first if both triggered)
        if position.is_stopped(bar.low, bar.high) {
            let exit_price = position.stop_price;
            let size = position.size;
            let fee = self.fill_model.calculate_fee(exit_price * size, false);
            self.position_tracker.close_position(
                bar.ts_min + 59_999,
                exit_price,
                size,
                fee,
                ExitReason::StopLoss,
            );
            return;
        }

        // Check TP1 (partial exit)
        if !position.tp1_hit && position.is_tp1_triggered(bar.low, bar.high) {
            if let Some(tp1_price) = position.tp1_price {
                let partial_size = position.size * self.config.tp1_pct;
                let fee = self.fill_model.calculate_fee(tp1_price * partial_size, false);
                self.position_tracker.close_position(
                    bar.ts_min + 59_999,
                    tp1_price,
                    partial_size,
                    fee,
                    ExitReason::TakeProfit1,
                );

                // Move stop to breakeven
                if self.config.move_stop_to_breakeven {
                    self.position_tracker.move_stop_to_breakeven();
                }
            }
        }

        // Check TP2 (full exit)
        if self.position_tracker.has_position() {
            let pos = self.position_tracker.position.as_ref().unwrap();
            if pos.is_tp2_triggered(bar.low, bar.high) {
                if let Some(tp2_price) = pos.tp2_price {
                    let size = pos.size;
                    let fee = self.fill_model.calculate_fee(tp2_price * size, false);
                    self.position_tracker.close_position(
                        bar.ts_min + 59_999,
                        tp2_price,
                        size,
                        fee,
                        ExitReason::TakeProfit2,
                    );
                }
            }
        }
    }

    /// Process funding (call periodically).
    pub fn process_funding(&mut self, ts_ms: TimestampMs, mark_price: f64) {
        let should_apply = match self.last_funding_ts {
            Some(last) => ts_ms - last >= self.funding_interval_ms,
            None => true,
        };

        if should_apply && self.position_tracker.has_position() {
            let pos = self.position_tracker.position.as_ref().unwrap();
            let notional = mark_price * pos.size;
            let funding = notional * self.config.funding_rate_8h_bps / 10000.0;

            // Longs pay when funding is positive
            let funding_cost = match pos.side {
                auction_core::PositionSide::Long => funding,
                auction_core::PositionSide::Short => -funding,
            };

            self.position_tracker.add_funding(funding_cost);
            self.last_funding_ts = Some(ts_ms);
        }
    }

    /// Get current position.
    pub fn position(&self) -> Option<&crate::position::Position> {
        self.position_tracker.position.as_ref()
    }

    /// Get all closed trades.
    pub fn trades(&self) -> &[ClosedTrade] {
        &self.position_tracker.trades
    }

    /// Get current equity.
    pub fn equity(&self) -> f64 {
        self.position_tracker.equity(self.config.initial_capital)
    }

    /// Calculate final metrics.
    pub fn calculate_metrics(&self) -> BacktestMetrics {
        self.metrics_calculator.calculate(&self.position_tracker.trades)
    }

    /// Reset the simulator.
    pub fn reset(&mut self) {
        self.position_tracker = PositionTracker::new();
        self.equity = self.config.initial_capital;
        self.last_funding_ts = None;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_quote(ts_ms: i64, bid: f64, ask: f64) -> Quote {
        Quote {
            ts_ms,
            bid_px: bid,
            bid_sz: 100.0,
            ask_px: ask,
            ask_sz: 100.0,
        }
    }

    fn make_bar(ts_min: i64, low: f64, high: f64, close: f64) -> Bar1m {
        Bar1m {
            ts_min,
            open: close,
            high,
            low,
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

    #[test]
    fn test_enter_long() {
        let mut sim = BacktestSimulator::new(BacktestConfig::default());

        let signal = Signal {
            ts_ms: 1000,
            action: Action::EnterLong,
            stop_price: Some(49500.0),
            tp1_price: Some(50500.0),
            tp2_price: Some(51000.0),
            size: Some(0.1),
            strategy_tag: "test".to_string(),
        };

        let quote = make_quote(1000, 50000.0, 50001.0);
        sim.process_signal(&signal, &quote);

        assert!(sim.position().is_some());
        assert_eq!(sim.position().unwrap().side, auction_core::PositionSide::Long);
    }

    #[test]
    fn test_stop_loss() {
        let mut sim = BacktestSimulator::new(BacktestConfig::default());

        // Enter long
        let signal = Signal {
            ts_ms: 1000,
            action: Action::EnterLong,
            stop_price: Some(49500.0),
            tp1_price: Some(50500.0),
            tp2_price: Some(51000.0),
            size: Some(0.1),
            strategy_tag: "test".to_string(),
        };

        let quote = make_quote(1000, 50000.0, 50001.0);
        sim.process_signal(&signal, &quote);

        // Bar that triggers stop
        let bar = make_bar(60_000, 49400.0, 50100.0, 49600.0);
        sim.check_stops_targets(&bar, &quote);

        assert!(sim.position().is_none());
        assert_eq!(sim.trades().len(), 1);
        assert_eq!(sim.trades()[0].exit_reason, ExitReason::StopLoss);
    }

    #[test]
    fn test_take_profit() {
        let config = BacktestConfig {
            tp1_pct: 0.30,
            move_stop_to_breakeven: true,
            ..Default::default()
        };
        let mut sim = BacktestSimulator::new(config);

        // Enter long
        let signal = Signal {
            ts_ms: 1000,
            action: Action::EnterLong,
            stop_price: Some(49500.0),
            tp1_price: Some(50500.0),
            tp2_price: Some(51000.0),
            size: Some(1.0),
            strategy_tag: "test".to_string(),
        };

        let quote = make_quote(1000, 50000.0, 50001.0);
        sim.process_signal(&signal, &quote);

        // Bar that triggers TP1
        let bar = make_bar(60_000, 50000.0, 50600.0, 50550.0);
        sim.check_stops_targets(&bar, &quote);

        // Should have partial exit
        assert!(sim.position().is_some());
        assert!((sim.position().unwrap().size - 0.7).abs() < 0.01);
        assert_eq!(sim.trades().len(), 1);
        assert_eq!(sim.trades()[0].exit_reason, ExitReason::TakeProfit1);
    }

    #[test]
    fn test_flip_position() {
        let mut sim = BacktestSimulator::new(BacktestConfig::default());

        // Enter long
        let long_signal = Signal {
            ts_ms: 1000,
            action: Action::EnterLong,
            stop_price: Some(49500.0),
            tp1_price: None,
            tp2_price: None,
            size: Some(0.1),
            strategy_tag: "test".to_string(),
        };

        let quote = make_quote(1000, 50000.0, 50001.0);
        sim.process_signal(&long_signal, &quote);

        assert!(sim.position().unwrap().side == auction_core::PositionSide::Long);

        // Flip to short
        let short_signal = Signal {
            ts_ms: 2000,
            action: Action::EnterShort,
            stop_price: Some(50500.0),
            tp1_price: None,
            tp2_price: None,
            size: Some(0.1),
            strategy_tag: "test".to_string(),
        };

        let quote2 = make_quote(2000, 50010.0, 50011.0);
        sim.process_signal(&short_signal, &quote2);

        assert!(sim.position().unwrap().side == auction_core::PositionSide::Short);
        assert_eq!(sim.trades().len(), 1); // One closed trade from flip
        assert_eq!(sim.trades()[0].exit_reason, ExitReason::SignalFlip);
    }
}
