//! Core types and configuration for the auction-trader system.
//!
//! This crate provides shared types used across all other crates:
//! - Market data types (trades, quotes, bars)
//! - Configuration structures
//! - Common error types

pub mod config;
pub mod error;
pub mod types;

pub use config::Config;
pub use error::{Error, Result};
pub use types::*;
