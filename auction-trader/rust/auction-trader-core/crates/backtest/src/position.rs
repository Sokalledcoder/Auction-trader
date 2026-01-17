//! Position tracking for backtesting.
//!
//! Tracks open positions, P&L, and generates fills.

use auction_core::{Fill, PositionSide, TimestampMs};

/// An open position.
#[derive(Debug, Clone)]
pub struct Position {
    /// Entry timestamp.
    pub entry_ts: TimestampMs,
    /// Position side.
    pub side: PositionSide,
    /// Entry price.
    pub entry_price: f64,
    /// Current size (may be reduced by partial exits).
    pub size: f64,
    /// Original size.
    pub original_size: f64,
    /// Stop price.
    pub stop_price: f64,
    /// TP1 price.
    pub tp1_price: Option<f64>,
    /// TP2 price.
    pub tp2_price: Option<f64>,
    /// Whether TP1 has been hit.
    pub tp1_hit: bool,
    /// Strategy tag (for analytics).
    pub strategy_tag: String,
    /// Total fees paid.
    pub fees_paid: f64,
    /// Total funding paid.
    pub funding_paid: f64,
}

impl Position {
    /// Calculate unrealized P&L at current price.
    pub fn unrealized_pnl(&self, current_price: f64) -> f64 {
        let price_diff = match self.side {
            PositionSide::Long => current_price - self.entry_price,
            PositionSide::Short => self.entry_price - current_price,
        };
        price_diff * self.size - self.fees_paid - self.funding_paid
    }

    /// Check if stop is triggered.
    pub fn is_stopped(&self, low: f64, high: f64) -> bool {
        match self.side {
            PositionSide::Long => low <= self.stop_price,
            PositionSide::Short => high >= self.stop_price,
        }
    }

    /// Check if TP1 is triggered.
    pub fn is_tp1_triggered(&self, low: f64, high: f64) -> bool {
        if self.tp1_hit {
            return false; // Already hit
        }
        match (self.side, self.tp1_price) {
            (PositionSide::Long, Some(tp)) => high >= tp,
            (PositionSide::Short, Some(tp)) => low <= tp,
            _ => false,
        }
    }

    /// Check if TP2 is triggered.
    pub fn is_tp2_triggered(&self, low: f64, high: f64) -> bool {
        match (self.side, self.tp2_price) {
            (PositionSide::Long, Some(tp)) => high >= tp,
            (PositionSide::Short, Some(tp)) => low <= tp,
            _ => false,
        }
    }
}

/// Closed trade record.
#[derive(Debug, Clone)]
pub struct ClosedTrade {
    /// Entry timestamp.
    pub entry_ts: TimestampMs,
    /// Exit timestamp.
    pub exit_ts: TimestampMs,
    /// Position side.
    pub side: PositionSide,
    /// Entry price.
    pub entry_price: f64,
    /// Exit price.
    pub exit_price: f64,
    /// Size.
    pub size: f64,
    /// Realized P&L.
    pub pnl: f64,
    /// Fees paid.
    pub fees: f64,
    /// Funding paid.
    pub funding: f64,
    /// Exit reason.
    pub exit_reason: ExitReason,
    /// Strategy tag.
    pub strategy_tag: String,
}

/// Reason for exiting a position.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExitReason {
    /// Stop loss hit.
    StopLoss,
    /// TP1 partial exit.
    TakeProfit1,
    /// TP2 full exit.
    TakeProfit2,
    /// Time stop.
    TimeStop,
    /// Signal flip.
    SignalFlip,
    /// Manual/other.
    Manual,
}

/// Position tracker for backtesting.
pub struct PositionTracker {
    /// Current open position.
    pub position: Option<Position>,
    /// Closed trades.
    pub trades: Vec<ClosedTrade>,
    /// Total realized P&L.
    pub total_pnl: f64,
    /// Total fees.
    pub total_fees: f64,
    /// Total funding.
    pub total_funding: f64,
    /// Win count.
    pub wins: u32,
    /// Loss count.
    pub losses: u32,
}

impl PositionTracker {
    /// Create a new position tracker.
    pub fn new() -> Self {
        Self {
            position: None,
            trades: Vec::new(),
            total_pnl: 0.0,
            total_fees: 0.0,
            total_funding: 0.0,
            wins: 0,
            losses: 0,
        }
    }

    /// Check if there's an open position.
    pub fn has_position(&self) -> bool {
        self.position.is_some()
    }

    /// Check if position is long.
    pub fn is_long(&self) -> bool {
        self.position.as_ref().map(|p| p.side == PositionSide::Long).unwrap_or(false)
    }

    /// Check if position is short.
    pub fn is_short(&self) -> bool {
        self.position.as_ref().map(|p| p.side == PositionSide::Short).unwrap_or(false)
    }

    /// Open a new position.
    pub fn open_position(&mut self, fill: Fill, stop_price: f64, tp1: Option<f64>, tp2: Option<f64>, strategy_tag: String) {
        self.position = Some(Position {
            entry_ts: fill.ts_ms,
            side: fill.side,
            entry_price: fill.price,
            size: fill.size,
            original_size: fill.size,
            stop_price,
            tp1_price: tp1,
            tp2_price: tp2,
            tp1_hit: false,
            strategy_tag,
            fees_paid: fill.fee,
            funding_paid: 0.0,
        });
    }

    /// Close position (full or partial).
    pub fn close_position(
        &mut self,
        ts_ms: TimestampMs,
        exit_price: f64,
        size: f64,
        exit_fee: f64,
        reason: ExitReason,
    ) -> Option<ClosedTrade> {
        let position = self.position.as_mut()?;

        // Calculate P&L for this portion
        let price_diff = match position.side {
            PositionSide::Long => exit_price - position.entry_price,
            PositionSide::Short => position.entry_price - exit_price,
        };

        // Pro-rate fees and funding
        let fee_portion = position.fees_paid * (size / position.original_size);
        let funding_portion = position.funding_paid * (size / position.original_size);
        let pnl = price_diff * size - fee_portion - funding_portion - exit_fee;

        let trade = ClosedTrade {
            entry_ts: position.entry_ts,
            exit_ts: ts_ms,
            side: position.side,
            entry_price: position.entry_price,
            exit_price,
            size,
            pnl,
            fees: fee_portion + exit_fee,
            funding: funding_portion,
            exit_reason: reason,
            strategy_tag: position.strategy_tag.clone(),
        };

        // Update totals
        self.total_pnl += pnl;
        self.total_fees += fee_portion + exit_fee;
        self.total_funding += funding_portion;

        if pnl > 0.0 {
            self.wins += 1;
        } else {
            self.losses += 1;
        }

        self.trades.push(trade.clone());

        // Update position size
        position.size -= size;

        // If fully closed, remove position
        if position.size <= 1e-10 {
            self.position = None;
        }

        Some(trade)
    }

    /// Move stop to breakeven.
    pub fn move_stop_to_breakeven(&mut self) {
        if let Some(pos) = &mut self.position {
            pos.stop_price = pos.entry_price;
            pos.tp1_hit = true;
        }
    }

    /// Add funding cost to current position.
    pub fn add_funding(&mut self, funding: f64) {
        if let Some(pos) = &mut self.position {
            pos.funding_paid += funding;
        }
        self.total_funding += funding;
    }

    /// Get current equity (starting capital + realized P&L).
    pub fn equity(&self, starting_capital: f64) -> f64 {
        starting_capital + self.total_pnl
    }

    /// Get win rate.
    pub fn win_rate(&self) -> f64 {
        let total = self.wins + self.losses;
        if total > 0 {
            self.wins as f64 / total as f64
        } else {
            0.0
        }
    }
}

impl Default for PositionTracker {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_fill(price: f64, size: f64, side: PositionSide) -> Fill {
        Fill {
            ts_ms: 1000,
            price,
            size,
            side,
            fee: 1.0,
            slippage: 0.1,
        }
    }

    #[test]
    fn test_open_close_long() {
        let mut tracker = PositionTracker::new();

        // Open long at 50000
        tracker.open_position(
            make_fill(50000.0, 0.1, PositionSide::Long),
            49500.0, // Stop
            Some(50500.0), // TP1
            Some(51000.0), // TP2
            "test".to_string(),
        );

        assert!(tracker.has_position());
        assert!(tracker.is_long());

        // Close at 50500 (profit)
        let trade = tracker.close_position(2000, 50500.0, 0.1, 1.0, ExitReason::TakeProfit1);

        assert!(trade.is_some());
        let trade = trade.unwrap();
        assert!((trade.pnl - 48.0).abs() < 1.0); // ~50 profit - 2 fees
        assert_eq!(tracker.wins, 1);
        assert!(!tracker.has_position());
    }

    #[test]
    fn test_partial_exit() {
        let mut tracker = PositionTracker::new();

        // Open long at 50000 with 1.0 size
        tracker.open_position(
            make_fill(50000.0, 1.0, PositionSide::Long),
            49500.0,
            Some(50500.0),
            Some(51000.0),
            "test".to_string(),
        );

        // Partial exit at TP1 (30%)
        tracker.close_position(2000, 50500.0, 0.3, 1.0, ExitReason::TakeProfit1);

        assert!(tracker.has_position());
        assert!((tracker.position.as_ref().unwrap().size - 0.7).abs() < 1e-10);

        // Full exit at TP2
        tracker.close_position(3000, 51000.0, 0.7, 1.0, ExitReason::TakeProfit2);

        assert!(!tracker.has_position());
        assert_eq!(tracker.trades.len(), 2);
    }

    #[test]
    fn test_stop_triggered() {
        let position = Position {
            entry_ts: 1000,
            side: PositionSide::Long,
            entry_price: 50000.0,
            size: 0.1,
            original_size: 0.1,
            stop_price: 49500.0,
            tp1_price: Some(50500.0),
            tp2_price: Some(51000.0),
            tp1_hit: false,
            strategy_tag: "test".to_string(),
            fees_paid: 1.0,
            funding_paid: 0.0,
        };

        // Low touches stop
        assert!(position.is_stopped(49400.0, 50200.0));

        // Low doesn't touch stop
        assert!(!position.is_stopped(49600.0, 50200.0));
    }
}
