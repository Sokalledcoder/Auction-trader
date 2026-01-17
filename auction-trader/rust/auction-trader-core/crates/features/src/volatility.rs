//! Rolling volatility computation.
//!
//! Computes standard deviation of log returns over a rolling window.

use std::collections::VecDeque;

/// Rolling volatility calculator using log returns.
pub struct RollingVolatility {
    /// Window size in periods.
    window: usize,
    /// Recent log returns.
    returns: VecDeque<f64>,
    /// Previous price (for computing next return).
    prev_price: Option<f64>,
    /// Running sum of returns (for mean).
    sum: f64,
    /// Running sum of squared returns (for variance).
    sum_sq: f64,
}

impl RollingVolatility {
    /// Create a new rolling volatility calculator.
    pub fn new(window: usize) -> Self {
        Self {
            window,
            returns: VecDeque::with_capacity(window),
            prev_price: None,
            sum: 0.0,
            sum_sq: 0.0,
        }
    }

    /// Add a price observation.
    ///
    /// Returns the current volatility if enough data is available.
    pub fn add_price(&mut self, price: f64) -> Option<f64> {
        if let Some(prev) = self.prev_price {
            if prev > 0.0 && price > 0.0 {
                let log_return = (price / prev).ln();
                self.add_return(log_return);
            }
        }
        self.prev_price = Some(price);
        self.volatility()
    }

    /// Add a log return directly.
    fn add_return(&mut self, ret: f64) {
        // If window is full, remove oldest
        if self.returns.len() >= self.window {
            if let Some(old) = self.returns.pop_front() {
                self.sum -= old;
                self.sum_sq -= old * old;
            }
        }

        // Add new return
        self.returns.push_back(ret);
        self.sum += ret;
        self.sum_sq += ret * ret;
    }

    /// Calculate current volatility (standard deviation of returns).
    pub fn volatility(&self) -> Option<f64> {
        let n = self.returns.len();
        if n < 2 {
            return None;
        }

        let n_f = n as f64;
        let mean = self.sum / n_f;
        let variance = (self.sum_sq / n_f) - (mean * mean);

        // Handle numerical issues
        if variance <= 0.0 {
            Some(0.0)
        } else {
            Some(variance.sqrt())
        }
    }

    /// Check if the window is full.
    pub fn is_ready(&self) -> bool {
        self.returns.len() >= self.window
    }

    /// Get the number of observations.
    pub fn count(&self) -> usize {
        self.returns.len()
    }

    /// Clear all data.
    pub fn clear(&mut self) {
        self.returns.clear();
        self.prev_price = None;
        self.sum = 0.0;
        self.sum_sq = 0.0;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_not_ready() {
        let vol = RollingVolatility::new(240);
        assert!(!vol.is_ready());
        assert!(vol.volatility().is_none());
    }

    #[test]
    fn test_constant_price() {
        let mut vol = RollingVolatility::new(5);

        // Constant price = zero volatility
        for _ in 0..10 {
            vol.add_price(100.0);
        }

        let sigma = vol.volatility().unwrap();
        assert!((sigma - 0.0).abs() < 1e-10);
    }

    #[test]
    fn test_alternating_price() {
        let mut vol = RollingVolatility::new(4);

        // Alternating price creates volatility
        vol.add_price(100.0);
        vol.add_price(101.0); // +1%
        vol.add_price(100.0); // -1%
        vol.add_price(101.0); // +1%
        vol.add_price(100.0); // -1%

        let sigma = vol.volatility().unwrap();
        assert!(sigma > 0.0);
    }

    #[test]
    fn test_rolling_window() {
        let mut vol = RollingVolatility::new(3);

        // Fill window
        vol.add_price(100.0);
        vol.add_price(101.0);
        vol.add_price(102.0);
        vol.add_price(103.0);

        assert_eq!(vol.count(), 3); // Only 3 returns in window

        // Add more
        vol.add_price(104.0);
        assert_eq!(vol.count(), 3); // Still 3 (oldest dropped)
    }

    #[test]
    fn test_known_volatility() {
        let mut vol = RollingVolatility::new(3);

        // Returns: 0.01, 0.02, 0.03
        // Mean: 0.02
        // Var: ((0.01-0.02)^2 + (0.02-0.02)^2 + (0.03-0.02)^2) / 3
        //    = (0.0001 + 0 + 0.0001) / 3 = 0.0000667
        // Std: sqrt(0.0000667) = 0.00816

        vol.add_price(100.0);
        vol.add_price(100.0 * (1.0_f64 + 0.01_f64).exp()); // ~101.005
        vol.add_price(100.0 * (1.0_f64 + 0.01_f64).exp() * (1.0_f64 + 0.02_f64).exp()); // ~103.05
        vol.add_price(100.0 * (1.0_f64 + 0.01_f64).exp() * (1.0_f64 + 0.02_f64).exp() * (1.0_f64 + 0.03_f64).exp());

        let sigma = vol.volatility().unwrap();
        // Approximately 0.00816
        assert!((sigma - 0.00816).abs() < 0.001);
    }
}
