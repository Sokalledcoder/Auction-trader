//! PyO3 bindings for auction-trader Rust components.
//!
//! Exposes high-performance Rust implementations to Python:
//! - Trade classification
//! - Bar building
//! - Feature computation (VA, OF, volatility)
//! - Backtesting engine

use pyo3::prelude::*;

use auction_core::{
    Trade as RustTrade,
    Quote as RustQuote,
    Bar1m as RustBar1m,
    ClassifiedTrade as RustClassifiedTrade,
    TradeSide as RustTradeSide,
    ValueArea as RustValueArea,
    OrderFlowMetrics as RustOrderFlowMetrics,
    Features1m as RustFeatures1m,
    Config as RustConfig,
};
use auction_ingestion::{TradeClassifier, BarBuilder};
use auction_features::FeatureEngine;

// ============================================================================
// Python-exposed Types
// ============================================================================

/// A single trade from the exchange.
#[pyclass]
#[derive(Clone)]
pub struct Trade {
    #[pyo3(get, set)]
    pub ts_ms: i64,
    #[pyo3(get, set)]
    pub price: f64,
    #[pyo3(get, set)]
    pub size: f64,
}

#[pymethods]
impl Trade {
    #[new]
    fn new(ts_ms: i64, price: f64, size: f64) -> Self {
        Trade { ts_ms, price, size }
    }

    fn __repr__(&self) -> String {
        format!("Trade(ts_ms={}, price={}, size={})", self.ts_ms, self.price, self.size)
    }
}

impl From<Trade> for RustTrade {
    fn from(t: Trade) -> Self {
        RustTrade {
            ts_ms: t.ts_ms,
            price: t.price,
            size: t.size,
        }
    }
}

impl From<RustTrade> for Trade {
    fn from(t: RustTrade) -> Self {
        Trade {
            ts_ms: t.ts_ms,
            price: t.price,
            size: t.size,
        }
    }
}

/// A Level 1 quote (best bid/ask).
#[pyclass]
#[derive(Clone)]
pub struct Quote {
    #[pyo3(get, set)]
    pub ts_ms: i64,
    #[pyo3(get, set)]
    pub bid_px: f64,
    #[pyo3(get, set)]
    pub bid_sz: f64,
    #[pyo3(get, set)]
    pub ask_px: f64,
    #[pyo3(get, set)]
    pub ask_sz: f64,
}

#[pymethods]
impl Quote {
    #[new]
    fn new(ts_ms: i64, bid_px: f64, bid_sz: f64, ask_px: f64, ask_sz: f64) -> Self {
        Quote { ts_ms, bid_px, bid_sz, ask_px, ask_sz }
    }

    #[getter]
    fn mid(&self) -> f64 {
        (self.bid_px + self.ask_px) / 2.0
    }

    #[getter]
    fn spread(&self) -> f64 {
        self.ask_px - self.bid_px
    }

    #[getter]
    fn imbalance(&self) -> f64 {
        let total = self.bid_sz + self.ask_sz;
        if total > 0.0 {
            (self.bid_sz - self.ask_sz) / total
        } else {
            0.0
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "Quote(ts_ms={}, bid={:.2}@{:.4}, ask={:.2}@{:.4})",
            self.ts_ms, self.bid_px, self.bid_sz, self.ask_px, self.ask_sz
        )
    }
}

impl From<Quote> for RustQuote {
    fn from(q: Quote) -> Self {
        RustQuote {
            ts_ms: q.ts_ms,
            bid_px: q.bid_px,
            bid_sz: q.bid_sz,
            ask_px: q.ask_px,
            ask_sz: q.ask_sz,
        }
    }
}

impl From<RustQuote> for Quote {
    fn from(q: RustQuote) -> Self {
        Quote {
            ts_ms: q.ts_ms,
            bid_px: q.bid_px,
            bid_sz: q.bid_sz,
            ask_px: q.ask_px,
            ask_sz: q.ask_sz,
        }
    }
}

/// Inferred trade side.
#[pyclass]
#[derive(Clone, Copy)]
pub enum TradeSide {
    Buy = 1,
    Sell = -1,
    Ambiguous = 0,
}

#[pymethods]
impl TradeSide {
    #[getter]
    fn sign(&self) -> i8 {
        match self {
            TradeSide::Buy => 1,
            TradeSide::Sell => -1,
            TradeSide::Ambiguous => 0,
        }
    }
}

impl From<RustTradeSide> for TradeSide {
    fn from(s: RustTradeSide) -> Self {
        match s {
            RustTradeSide::Buy => TradeSide::Buy,
            RustTradeSide::Sell => TradeSide::Sell,
            RustTradeSide::Ambiguous => TradeSide::Ambiguous,
        }
    }
}

/// A trade with inferred side.
#[pyclass]
#[derive(Clone)]
pub struct ClassifiedTrade {
    #[pyo3(get)]
    pub trade: Trade,
    #[pyo3(get)]
    pub side: TradeSide,
    #[pyo3(get)]
    pub quote_bid_px: f64,
    #[pyo3(get)]
    pub quote_ask_px: f64,
    #[pyo3(get)]
    pub quote_staleness_ms: i64,
}

#[pymethods]
impl ClassifiedTrade {
    #[getter]
    fn signed_size(&self) -> f64 {
        self.trade.size * self.side.sign() as f64
    }
}

impl From<RustClassifiedTrade> for ClassifiedTrade {
    fn from(ct: RustClassifiedTrade) -> Self {
        ClassifiedTrade {
            trade: ct.trade.into(),
            side: ct.side.into(),
            quote_bid_px: ct.quote_bid_px,
            quote_ask_px: ct.quote_ask_px,
            quote_staleness_ms: ct.quote_staleness_ms,
        }
    }
}

/// 1-minute OHLCV bar with L1 snapshot.
#[pyclass]
#[derive(Clone)]
pub struct Bar1m {
    #[pyo3(get)]
    pub ts_min: i64,
    #[pyo3(get)]
    pub open: f64,
    #[pyo3(get)]
    pub high: f64,
    #[pyo3(get)]
    pub low: f64,
    #[pyo3(get)]
    pub close: f64,
    #[pyo3(get)]
    pub volume: f64,
    #[pyo3(get)]
    pub vwap: Option<f64>,
    #[pyo3(get)]
    pub trade_count: u32,
    #[pyo3(get)]
    pub bid_px_close: f64,
    #[pyo3(get)]
    pub ask_px_close: f64,
    #[pyo3(get)]
    pub bid_sz_close: f64,
    #[pyo3(get)]
    pub ask_sz_close: f64,
}

#[pymethods]
impl Bar1m {
    #[getter]
    fn mid_close(&self) -> f64 {
        (self.bid_px_close + self.ask_px_close) / 2.0
    }

    #[getter]
    fn spread_close(&self) -> f64 {
        self.ask_px_close - self.bid_px_close
    }

    #[getter]
    fn qimb_close(&self) -> f64 {
        let total = self.bid_sz_close + self.ask_sz_close;
        if total > 0.0 {
            (self.bid_sz_close - self.ask_sz_close) / total
        } else {
            0.0
        }
    }
}

impl From<RustBar1m> for Bar1m {
    fn from(b: RustBar1m) -> Self {
        Bar1m {
            ts_min: b.ts_min,
            open: b.open,
            high: b.high,
            low: b.low,
            close: b.close,
            volume: b.volume,
            vwap: b.vwap,
            trade_count: b.trade_count,
            bid_px_close: b.bid_px_close,
            ask_px_close: b.ask_px_close,
            bid_sz_close: b.bid_sz_close,
            ask_sz_close: b.ask_sz_close,
        }
    }
}

/// Value Area output.
#[pyclass]
#[derive(Clone)]
pub struct ValueArea {
    #[pyo3(get)]
    pub poc: f64,
    #[pyo3(get)]
    pub vah: f64,
    #[pyo3(get)]
    pub val: f64,
    #[pyo3(get)]
    pub coverage: f64,
    #[pyo3(get)]
    pub bin_count: usize,
    #[pyo3(get)]
    pub total_volume: f64,
    #[pyo3(get)]
    pub bin_width: f64,
    #[pyo3(get)]
    pub is_valid: bool,
}

impl From<RustValueArea> for ValueArea {
    fn from(va: RustValueArea) -> Self {
        ValueArea {
            poc: va.poc,
            vah: va.vah,
            val: va.val,
            coverage: va.coverage,
            bin_count: va.bin_count as usize,
            total_volume: va.total_volume,
            bin_width: va.bin_width,
            is_valid: va.is_valid,
        }
    }
}

/// Order flow metrics.
#[pyclass]
#[derive(Clone)]
pub struct OrderFlowMetrics {
    #[pyo3(get)]
    pub of_1m: f64,
    #[pyo3(get)]
    pub of_norm_1m: f64,
    #[pyo3(get)]
    pub total_volume: f64,
    #[pyo3(get)]
    pub buy_volume: f64,
    #[pyo3(get)]
    pub sell_volume: f64,
    #[pyo3(get)]
    pub ambiguous_volume: f64,
    #[pyo3(get)]
    pub ambiguous_frac: f64,
}

impl From<RustOrderFlowMetrics> for OrderFlowMetrics {
    fn from(of: RustOrderFlowMetrics) -> Self {
        OrderFlowMetrics {
            of_1m: of.of_1m,
            of_norm_1m: of.of_norm_1m,
            total_volume: of.total_volume,
            buy_volume: of.buy_volume,
            sell_volume: of.sell_volume,
            ambiguous_volume: of.ambiguous_volume,
            ambiguous_frac: of.ambiguous_frac,
        }
    }
}

/// Complete feature set for a minute.
#[pyclass]
#[derive(Clone)]
pub struct Features1m {
    #[pyo3(get)]
    pub ts_min: i64,
    #[pyo3(get)]
    pub mid_close: f64,
    #[pyo3(get)]
    pub sigma_240: f64,
    #[pyo3(get)]
    pub bin_width: f64,
    #[pyo3(get)]
    pub va: ValueArea,
    #[pyo3(get)]
    pub order_flow: OrderFlowMetrics,
    #[pyo3(get)]
    pub qimb_close: f64,
    #[pyo3(get)]
    pub qimb_ema: f64,
    #[pyo3(get)]
    pub spread_avg_60m: f64,
}

impl From<RustFeatures1m> for Features1m {
    fn from(f: RustFeatures1m) -> Self {
        Features1m {
            ts_min: f.ts_min,
            mid_close: f.mid_close,
            sigma_240: f.sigma_240,
            bin_width: f.bin_width,
            va: f.va.into(),
            order_flow: f.order_flow.into(),
            qimb_close: f.qimb_close,
            qimb_ema: f.qimb_ema,
            spread_avg_60m: f.spread_avg_60m,
        }
    }
}

// ============================================================================
// Python-exposed Engine Classes
// ============================================================================

/// Trade classifier with quote alignment.
#[pyclass]
pub struct PyTradeClassifier {
    inner: TradeClassifier,
}

#[pymethods]
impl PyTradeClassifier {
    #[new]
    fn new(max_quote_staleness_ms: i64, use_tick_rule_fallback: bool) -> Self {
        PyTradeClassifier {
            inner: TradeClassifier::new(max_quote_staleness_ms, use_tick_rule_fallback),
        }
    }

    /// Add a quote for trade classification.
    fn add_quote(&mut self, quote: Quote) {
        self.inner.add_quote(quote.into());
    }

    /// Classify a single trade.
    fn classify(&mut self, trade: Trade) -> ClassifiedTrade {
        self.inner.classify(trade.into()).into()
    }

    /// Classify a batch of trades.
    fn classify_batch(&mut self, trades: Vec<Trade>) -> Vec<ClassifiedTrade> {
        let rust_trades: Vec<RustTrade> = trades.into_iter().map(|t| t.into()).collect();
        self.inner
            .classify_batch(rust_trades)
            .into_iter()
            .map(|ct| ct.into())
            .collect()
    }

    /// Get classification statistics.
    fn stats(&self) -> (u64, u64, u64, u64) {
        let s = self.inner.stats();
        (s.total_trades, s.buy_trades, s.sell_trades, s.ambiguous_trades)
    }

    /// Reset statistics.
    fn reset_stats(&mut self) {
        self.inner.reset_stats();
    }

    /// Clear all state.
    fn clear(&mut self) {
        self.inner.clear();
    }
}

/// Bar builder for aggregating trades into 1-minute bars.
#[pyclass]
pub struct PyBarBuilder {
    inner: BarBuilder,
}

#[pymethods]
impl PyBarBuilder {
    #[new]
    fn new() -> Self {
        PyBarBuilder {
            inner: BarBuilder::new(),
        }
    }

    /// Add a quote for close snapshot.
    fn add_quote(&mut self, quote: Quote) {
        self.inner.add_quote(quote.into());
    }

    /// Add a classified trade.
    fn add_trade(&mut self, trade: ClassifiedTrade) {
        let rust_ct = RustClassifiedTrade {
            trade: RustTrade {
                ts_ms: trade.trade.ts_ms,
                price: trade.trade.price,
                size: trade.trade.size,
            },
            side: match trade.side {
                TradeSide::Buy => RustTradeSide::Buy,
                TradeSide::Sell => RustTradeSide::Sell,
                TradeSide::Ambiguous => RustTradeSide::Ambiguous,
            },
            quote_bid_px: trade.quote_bid_px,
            quote_ask_px: trade.quote_ask_px,
            quote_staleness_ms: trade.quote_staleness_ms,
        };
        self.inner.add_trade(&rust_ct);
    }

    /// Finalize and emit bars before a timestamp.
    fn finalize_before(&mut self, current_ts_ms: i64) -> Vec<Bar1m> {
        self.inner
            .finalize_before(current_ts_ms)
            .into_iter()
            .map(|b| b.into())
            .collect()
    }

    /// Force finalize a specific minute.
    fn force_finalize(&mut self, ts_min: i64) -> Option<Bar1m> {
        self.inner.force_finalize(ts_min).map(|b| b.into())
    }

    /// Get number of pending bars.
    fn pending_bar_count(&self) -> usize {
        self.inner.pending_bar_count()
    }

    /// Clear all state.
    fn clear(&mut self) {
        self.inner.clear();
    }
}

/// Feature computation engine.
#[pyclass]
pub struct PyFeatureEngine {
    inner: FeatureEngine,
}

impl PyFeatureEngine {
    fn bar_to_rust(bar: &Bar1m) -> RustBar1m {
        RustBar1m {
            ts_min: bar.ts_min,
            open: bar.open,
            high: bar.high,
            low: bar.low,
            close: bar.close,
            volume: bar.volume,
            vwap: bar.vwap,
            trade_count: bar.trade_count,
            bid_px_close: bar.bid_px_close,
            ask_px_close: bar.ask_px_close,
            bid_sz_close: bar.bid_sz_close,
            ask_sz_close: bar.ask_sz_close,
        }
    }
}

#[pymethods]
impl PyFeatureEngine {
    #[new]
    fn new() -> Self {
        let config = RustConfig::default();
        PyFeatureEngine {
            inner: FeatureEngine::new(&config),
        }
    }

    /// Create from a custom config.
    #[staticmethod]
    fn with_config(
        rolling_window_minutes: u32,
        va_fraction: f64,
        tick_size: f64,
        alpha_bin: f64,
        bin_width_max_ticks: u32,
        min_va_bins: u32,
    ) -> Self {
        let mut config = RustConfig::default();
        config.instrument.rolling_window_minutes = rolling_window_minutes;
        config.value_area.va_fraction = va_fraction;
        config.instrument.tick_size = tick_size;
        config.value_area.alpha_bin = alpha_bin;
        config.value_area.bin_width_max_ticks = bin_width_max_ticks;
        config.value_area.min_va_bins = min_va_bins;
        PyFeatureEngine {
            inner: FeatureEngine::new(&config),
        }
    }

    /// Add a quote to the engine.
    fn add_quote(&mut self, quote: &Quote) {
        self.inner.add_quote(&quote.clone().into());
    }

    /// Add a classified trade to the engine.
    fn add_trade(&mut self, trade: &ClassifiedTrade) {
        let rust_ct = RustClassifiedTrade {
            trade: RustTrade {
                ts_ms: trade.trade.ts_ms,
                price: trade.trade.price,
                size: trade.trade.size,
            },
            side: match trade.side {
                TradeSide::Buy => RustTradeSide::Buy,
                TradeSide::Sell => RustTradeSide::Sell,
                TradeSide::Ambiguous => RustTradeSide::Ambiguous,
            },
            quote_bid_px: trade.quote_bid_px,
            quote_ask_px: trade.quote_ask_px,
            quote_staleness_ms: trade.quote_staleness_ms,
        };
        self.inner.add_trade(&rust_ct);
    }

    /// Add a bar to the engine.
    fn add_bar(&mut self, bar: &Bar1m) {
        self.inner.add_bar(&Self::bar_to_rust(bar));
    }

    /// Compute features for the current state.
    fn compute_features(&self, ts_min: i64, bar: &Bar1m) -> Features1m {
        self.inner.compute_features(ts_min, &Self::bar_to_rust(bar)).into()
    }

    /// Check if the engine has enough warmup data.
    fn is_ready(&self) -> bool {
        self.inner.is_ready()
    }

    /// Get the current bin width.
    fn current_bin_width(&self) -> f64 {
        self.inner.current_bin_width()
    }

    /// Clear all state.
    fn clear(&mut self) {
        self.inner.clear();
    }
}

// ============================================================================
// Module Definition
// ============================================================================

/// Auction Trader Core - High-performance Rust components for Python.
#[pymodule]
fn auction_trader_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Types
    m.add_class::<Trade>()?;
    m.add_class::<Quote>()?;
    m.add_class::<TradeSide>()?;
    m.add_class::<ClassifiedTrade>()?;
    m.add_class::<Bar1m>()?;
    m.add_class::<ValueArea>()?;
    m.add_class::<OrderFlowMetrics>()?;
    m.add_class::<Features1m>()?;

    // Engine classes
    m.add_class::<PyTradeClassifier>()?;
    m.add_class::<PyBarBuilder>()?;
    m.add_class::<PyFeatureEngine>()?;

    Ok(())
}
