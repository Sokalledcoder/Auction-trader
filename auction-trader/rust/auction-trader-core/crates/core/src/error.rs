//! Error types for the auction-trader system.

use thiserror::Error;

/// Result type alias using our Error type.
pub type Result<T> = std::result::Result<T, Error>;

/// Main error type for the auction-trader system.
#[derive(Error, Debug)]
pub enum Error {
    /// Configuration error.
    #[error("Configuration error: {0}")]
    Config(String),

    /// Data error (invalid or missing data).
    #[error("Data error: {0}")]
    Data(String),

    /// Insufficient data for computation.
    #[error("Insufficient data: {0}")]
    InsufficientData(String),

    /// Value Area computation error.
    #[error("Value Area error: {0}")]
    ValueArea(String),

    /// Order flow computation error.
    #[error("Order flow error: {0}")]
    OrderFlow(String),

    /// Signal generation error.
    #[error("Signal error: {0}")]
    Signal(String),

    /// Execution error.
    #[error("Execution error: {0}")]
    Execution(String),

    /// Database error.
    #[error("Database error: {0}")]
    Database(String),

    /// I/O error.
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),

    /// JSON serialization/deserialization error.
    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),

    /// Generic error with message.
    #[error("{0}")]
    Other(String),
}

impl Error {
    /// Create a configuration error.
    pub fn config(msg: impl Into<String>) -> Self {
        Error::Config(msg.into())
    }

    /// Create a data error.
    pub fn data(msg: impl Into<String>) -> Self {
        Error::Data(msg.into())
    }

    /// Create an insufficient data error.
    pub fn insufficient_data(msg: impl Into<String>) -> Self {
        Error::InsufficientData(msg.into())
    }

    /// Create a Value Area error.
    pub fn value_area(msg: impl Into<String>) -> Self {
        Error::ValueArea(msg.into())
    }

    /// Create an order flow error.
    pub fn order_flow(msg: impl Into<String>) -> Self {
        Error::OrderFlow(msg.into())
    }

    /// Create a signal error.
    pub fn signal(msg: impl Into<String>) -> Self {
        Error::Signal(msg.into())
    }

    /// Create an execution error.
    pub fn execution(msg: impl Into<String>) -> Self {
        Error::Execution(msg.into())
    }

    /// Create a database error.
    pub fn database(msg: impl Into<String>) -> Self {
        Error::Database(msg.into())
    }
}
