//! Feature computation for the auction-trader system.
//!
//! This crate handles:
//! - Rolling volatility (sigma_240)
//! - Rolling volume-at-price histogram
//! - Value Area computation (POC, VAH, VAL)
//! - Order flow metrics aggregation
//! - Quote imbalance computation

pub mod volatility;
pub mod histogram;
pub mod value_area;
pub mod order_flow;
pub mod engine;

pub use volatility::RollingVolatility;
pub use histogram::RollingHistogram;
pub use value_area::ValueAreaComputer;
pub use order_flow::OrderFlowAggregator;
pub use engine::FeatureEngine;
