//! Core data types for the auction-trader system.

use chrono::{DateTime, Utc};
use ordered_float::OrderedFloat;
use serde::{Deserialize, Serialize};

/// Timestamp in milliseconds since Unix epoch (UTC).
pub type TimestampMs = i64;

/// Price type with ordering support.
pub type Price = OrderedFloat<f64>;

/// Size/quantity type.
pub type Size = f64;

/// Convert a timestamp to minute boundary.
#[inline]
pub fn ts_to_minute(ts_ms: TimestampMs) -> TimestampMs {
    (ts_ms / 60_000) * 60_000
}

/// A single trade (print) from the exchange.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Trade {
    /// Timestamp in milliseconds.
    pub ts_ms: TimestampMs,
    /// Trade price.
    pub price: f64,
    /// Trade size (contracts or BTC).
    pub size: Size,
}

/// A Level 1 quote (best bid/ask).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Quote {
    /// Timestamp in milliseconds.
    pub ts_ms: TimestampMs,
    /// Best bid price.
    pub bid_px: f64,
    /// Best bid size.
    pub bid_sz: Size,
    /// Best ask price.
    pub ask_px: f64,
    /// Best ask size.
    pub ask_sz: Size,
}

impl Quote {
    /// Calculate mid price.
    #[inline]
    pub fn mid(&self) -> f64 {
        (self.bid_px + self.ask_px) / 2.0
    }

    /// Calculate spread.
    #[inline]
    pub fn spread(&self) -> f64 {
        self.ask_px - self.bid_px
    }

    /// Calculate quote imbalance: (bid_sz - ask_sz) / (bid_sz + ask_sz).
    #[inline]
    pub fn imbalance(&self) -> f64 {
        let total = self.bid_sz + self.ask_sz;
        if total > 0.0 {
            (self.bid_sz - self.ask_sz) / total
        } else {
            0.0
        }
    }
}

/// Inferred trade side from bid/ask alignment.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[repr(i8)]
pub enum TradeSide {
    /// Trade at or above ask (buyer-initiated).
    Buy = 1,
    /// Trade at or below bid (seller-initiated).
    Sell = -1,
    /// Trade between bid and ask (ambiguous).
    Ambiguous = 0,
}

impl TradeSide {
    /// Get the sign as i8.
    #[inline]
    pub fn sign(self) -> i8 {
        self as i8
    }

    /// Get the sign as f64.
    #[inline]
    pub fn sign_f64(self) -> f64 {
        self.sign() as f64
    }
}

/// A trade with inferred side and associated quote data.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ClassifiedTrade {
    /// Original trade.
    pub trade: Trade,
    /// Inferred side.
    pub side: TradeSide,
    /// Quote used for classification.
    pub quote_bid_px: f64,
    pub quote_ask_px: f64,
    /// Staleness of quote relative to trade (ms).
    pub quote_staleness_ms: i64,
}

impl ClassifiedTrade {
    /// Get signed size (positive for buy, negative for sell, zero for ambiguous).
    #[inline]
    pub fn signed_size(&self) -> f64 {
        self.trade.size * self.side.sign_f64()
    }
}

/// 1-minute OHLCV bar with L1 snapshot at close.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Bar1m {
    /// Minute boundary timestamp (ms).
    pub ts_min: TimestampMs,
    /// Open price.
    pub open: f64,
    /// High price.
    pub high: f64,
    /// Low price.
    pub low: f64,
    /// Close price.
    pub close: f64,
    /// Total volume.
    pub volume: Size,
    /// VWAP (optional).
    pub vwap: Option<f64>,
    /// Number of trades.
    pub trade_count: u32,
    /// L1 bid price at close.
    pub bid_px_close: f64,
    /// L1 ask price at close.
    pub ask_px_close: f64,
    /// L1 bid size at close.
    pub bid_sz_close: Size,
    /// L1 ask size at close.
    pub ask_sz_close: Size,
}

impl Bar1m {
    /// Calculate mid price at close.
    #[inline]
    pub fn mid_close(&self) -> f64 {
        (self.bid_px_close + self.ask_px_close) / 2.0
    }

    /// Calculate spread at close.
    #[inline]
    pub fn spread_close(&self) -> f64 {
        self.ask_px_close - self.bid_px_close
    }

    /// Calculate quote imbalance at close.
    #[inline]
    pub fn qimb_close(&self) -> f64 {
        let total = self.bid_sz_close + self.ask_sz_close;
        if total > 0.0 {
            (self.bid_sz_close - self.ask_sz_close) / total
        } else {
            0.0
        }
    }
}

/// Value Area output.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ValueArea {
    /// Point of Control (price with max volume).
    pub poc: f64,
    /// Value Area High.
    pub vah: f64,
    /// Value Area Low.
    pub val: f64,
    /// Actual coverage achieved (e.g., 0.70).
    pub coverage: f64,
    /// Number of bins in the VA.
    pub bin_count: u32,
    /// Total volume in the 4h window.
    pub total_volume: Size,
    /// Current bin width used.
    pub bin_width: f64,
    /// Whether the VA is valid (enough bins).
    pub is_valid: bool,
}

impl ValueArea {
    /// Create an invalid/empty VA.
    pub fn invalid() -> Self {
        Self {
            poc: 0.0,
            vah: 0.0,
            val: 0.0,
            coverage: 0.0,
            bin_count: 0,
            total_volume: 0.0,
            bin_width: 0.0,
            is_valid: false,
        }
    }
}

/// Order flow metrics for a 1-minute period.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrderFlowMetrics {
    /// Net signed order flow (sum of signed sizes).
    pub of_1m: f64,
    /// Normalized order flow (of_1m / total_volume).
    pub of_norm_1m: f64,
    /// Total volume in the minute.
    pub total_volume: Size,
    /// Buy volume (sum of buy-initiated trades).
    pub buy_volume: Size,
    /// Sell volume (sum of sell-initiated trades).
    pub sell_volume: Size,
    /// Ambiguous volume.
    pub ambiguous_volume: Size,
    /// Fraction of volume that was ambiguous.
    pub ambiguous_frac: f64,
}

impl OrderFlowMetrics {
    /// Check if ambiguous fraction is above threshold.
    pub fn is_high_ambiguous(&self, threshold: f64) -> bool {
        self.ambiguous_frac > threshold
    }
}

/// Complete feature set for a 1-minute period.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Features1m {
    /// Minute boundary timestamp.
    pub ts_min: TimestampMs,
    /// Mid price at close.
    pub mid_close: f64,
    /// Rolling 4h volatility (stdev of log returns).
    pub sigma_240: f64,
    /// Current bin width.
    pub bin_width: f64,
    /// Value Area.
    pub va: ValueArea,
    /// Order flow metrics.
    pub order_flow: OrderFlowMetrics,
    /// Quote imbalance at close.
    pub qimb_close: f64,
    /// EMA of quote imbalance over the minute.
    pub qimb_ema: f64,
    /// Rolling 60-min average spread.
    pub spread_avg_60m: f64,
}

/// Trading signal type.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum SignalType {
    /// Break-in long (failed auction below VAL, close back above).
    BreakinLong,
    /// Break-in short (failed auction above VAH, close back below).
    BreakinShort,
    /// Breakout long (acceptance above VAH).
    BreakoutLong,
    /// Breakout short (acceptance below VAL).
    BreakoutShort,
    /// Failed breakout long (fakeout below VAL, reversal up).
    FailedBreakoutLong,
    /// Failed breakout short (fakeout above VAH, reversal down).
    FailedBreakoutShort,
}

impl SignalType {
    /// Is this a long signal?
    pub fn is_long(self) -> bool {
        matches!(
            self,
            SignalType::BreakinLong | SignalType::BreakoutLong | SignalType::FailedBreakoutLong
        )
    }

    /// Is this a short signal?
    pub fn is_short(self) -> bool {
        !self.is_long()
    }

    /// Get the priority (lower = higher priority).
    /// Break-in (1) > Failed breakout (2) > Breakout (3)
    pub fn priority(self) -> u8 {
        match self {
            SignalType::BreakinLong | SignalType::BreakinShort => 1,
            SignalType::FailedBreakoutLong | SignalType::FailedBreakoutShort => 2,
            SignalType::BreakoutLong | SignalType::BreakoutShort => 3,
        }
    }
}

/// Position side.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum PositionSide {
    Long,
    Short,
}

impl PositionSide {
    /// Get sign: +1 for long, -1 for short.
    pub fn sign(self) -> f64 {
        match self {
            PositionSide::Long => 1.0,
            PositionSide::Short => -1.0,
        }
    }
}

/// Action to take.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Action {
    /// Enter a long position.
    EnterLong,
    /// Enter a short position.
    EnterShort,
    /// Exit current position.
    Exit,
    /// Hold / do nothing.
    Hold,
}

/// Fill information for a simulated or real trade.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Fill {
    /// Timestamp of fill.
    pub ts_ms: TimestampMs,
    /// Fill price.
    pub price: f64,
    /// Fill size (positive).
    pub size: Size,
    /// Side of the fill.
    pub side: PositionSide,
    /// Fee paid (positive).
    pub fee: f64,
    /// Slippage from expected price.
    pub slippage: f64,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_ts_to_minute() {
        // 2024-01-01 00:01:30.500 -> 2024-01-01 00:01:00.000
        let ts = 1704067290500i64;
        let minute = ts_to_minute(ts);
        assert_eq!(minute, 1704067260000);
    }

    #[test]
    fn test_quote_mid() {
        let quote = Quote {
            ts_ms: 0,
            bid_px: 50000.0,
            bid_sz: 1.0,
            ask_px: 50010.0,
            ask_sz: 1.0,
        };
        assert!((quote.mid() - 50005.0).abs() < 1e-10);
    }

    #[test]
    fn test_quote_imbalance() {
        let quote = Quote {
            ts_ms: 0,
            bid_px: 50000.0,
            bid_sz: 100.0,
            ask_px: 50010.0,
            ask_sz: 50.0,
        };
        // (100 - 50) / (100 + 50) = 50/150 = 0.333...
        assert!((quote.imbalance() - 0.3333333).abs() < 0.001);
    }

    #[test]
    fn test_trade_side_sign() {
        assert_eq!(TradeSide::Buy.sign(), 1);
        assert_eq!(TradeSide::Sell.sign(), -1);
        assert_eq!(TradeSide::Ambiguous.sign(), 0);
    }

    #[test]
    fn test_signal_priority() {
        assert!(SignalType::BreakinLong.priority() < SignalType::FailedBreakoutLong.priority());
        assert!(SignalType::FailedBreakoutShort.priority() < SignalType::BreakoutShort.priority());
    }
}
