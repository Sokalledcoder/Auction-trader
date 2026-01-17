//! Data ingestion and normalization for the auction-trader system.
//!
//! This crate handles:
//! - Trade-quote alignment
//! - Trade side inference (bid/ask classification)
//! - Minute bar building
//! - Trade aggregation (same-timestamp trades)

pub mod classifier;
pub mod bar_builder;

pub use classifier::{TradeClassifier, ClassificationStats};
pub use bar_builder::BarBuilder;
