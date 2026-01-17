//! Backtesting engine for the auction-trader system.
//!
//! This crate provides:
//! - Tick-level replay simulation
//! - Bid/ask fill modeling
//! - Fee and slippage accounting
//! - Position tracking and P&L calculation

pub mod fill_model;
pub mod simulator;
pub mod position;
pub mod metrics;

pub use fill_model::FillModel;
pub use simulator::BacktestSimulator;
pub use position::PositionTracker;
pub use metrics::BacktestMetrics;
