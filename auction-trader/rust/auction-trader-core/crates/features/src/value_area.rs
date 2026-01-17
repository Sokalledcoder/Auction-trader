//! Value Area computation (POC, VAH, VAL).
//!
//! Computes Point of Control and Value Area boundaries from a volume histogram.

use auction_core::ValueArea;
use ordered_float::OrderedFloat;
use std::collections::BTreeMap;

/// Configuration for Value Area computation.
#[derive(Debug, Clone)]
pub struct ValueAreaConfig {
    /// Target VA coverage (e.g., 0.70 for 70%).
    pub va_fraction: f64,
    /// Minimum number of bins for valid VA.
    pub min_bins: u32,
}

impl Default for ValueAreaConfig {
    fn default() -> Self {
        Self {
            va_fraction: 0.70,
            min_bins: 20,
        }
    }
}

/// Value Area computer.
pub struct ValueAreaComputer {
    config: ValueAreaConfig,
}

impl ValueAreaComputer {
    /// Create a new Value Area computer.
    pub fn new(config: ValueAreaConfig) -> Self {
        Self { config }
    }

    /// Compute Value Area from a histogram.
    ///
    /// The histogram should be keyed by bin price (lower edge) with volume values.
    pub fn compute(&self, histogram: &BTreeMap<OrderedFloat<f64>, f64>, bin_width: f64) -> ValueArea {
        // Check minimum bins
        if histogram.len() < self.config.min_bins as usize {
            return ValueArea::invalid();
        }

        // Calculate total volume
        let total_volume: f64 = histogram.values().sum();
        if total_volume <= 0.0 {
            return ValueArea::invalid();
        }

        // Find POC (bin with maximum volume)
        let (poc_bin, poc_volume) = histogram
            .iter()
            .max_by(|a, b| a.1.partial_cmp(b.1).unwrap_or(std::cmp::Ordering::Equal))
            .map(|(k, v)| (k.0, *v))
            .unwrap_or((0.0, 0.0));

        // Target volume for VA
        let target_volume = total_volume * self.config.va_fraction;

        // Get sorted bins for expansion
        let bins: Vec<(f64, f64)> = histogram
            .iter()
            .map(|(k, v)| (k.0, *v))
            .collect();

        // Find POC index
        let poc_idx = bins.iter().position(|(p, _)| (*p - poc_bin).abs() < 1e-10).unwrap_or(0);

        // Expand outward from POC
        let mut cumulative_volume = poc_volume;
        let mut low_idx = poc_idx;
        let mut high_idx = poc_idx;
        let mut included_bins = 1u32;

        while cumulative_volume < target_volume {
            // Look at next candidates
            let next_low = if low_idx > 0 { Some(low_idx - 1) } else { None };
            let next_high = if high_idx < bins.len() - 1 { Some(high_idx + 1) } else { None };

            // Choose the one with higher volume (expand to higher-volume adjacent bin)
            let expand_low = match (next_low, next_high) {
                (Some(l), Some(h)) => bins[l].1 >= bins[h].1,
                (Some(_), None) => true,
                (None, Some(_)) => false,
                (None, None) => break, // Can't expand further
            };

            if expand_low {
                low_idx = next_low.unwrap();
                cumulative_volume += bins[low_idx].1;
            } else {
                high_idx = next_high.unwrap();
                cumulative_volume += bins[high_idx].1;
            }
            included_bins += 1;
        }

        // VA boundaries
        let val = bins[low_idx].0;
        let vah = bins[high_idx].0 + bin_width; // VAH is upper edge of highest bin

        // Coverage achieved
        let coverage = cumulative_volume / total_volume;

        ValueArea {
            poc: poc_bin + bin_width / 2.0, // POC is mid-point of bin
            vah,
            val,
            coverage,
            bin_count: included_bins,
            total_volume,
            bin_width,
            is_valid: true,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_histogram(data: &[(f64, f64)]) -> BTreeMap<OrderedFloat<f64>, f64> {
        data.iter().map(|(k, v)| (OrderedFloat(*k), *v)).collect()
    }

    #[test]
    fn test_simple_va() {
        let computer = ValueAreaComputer::new(ValueAreaConfig {
            va_fraction: 0.70,
            min_bins: 3,
        });

        // Symmetric histogram around 100
        let hist = make_histogram(&[
            (98.0, 50.0),
            (99.0, 100.0),
            (100.0, 200.0), // POC
            (101.0, 100.0),
            (102.0, 50.0),
        ]);

        let va = computer.compute(&hist, 1.0);

        assert!(va.is_valid);
        assert!((va.poc - 100.5).abs() < 1e-10); // Mid-point of POC bin
        assert!((va.total_volume - 500.0).abs() < 1e-10);
    }

    #[test]
    fn test_asymmetric_va() {
        let computer = ValueAreaComputer::new(ValueAreaConfig {
            va_fraction: 0.70,
            min_bins: 3,
        });

        // Asymmetric histogram (more volume above POC)
        let hist = make_histogram(&[
            (98.0, 10.0),
            (99.0, 20.0),
            (100.0, 100.0), // POC
            (101.0, 80.0),
            (102.0, 60.0),
        ]);

        let va = computer.compute(&hist, 1.0);

        assert!(va.is_valid);
        // Should expand more to the upside
        assert!((va.poc - 100.5).abs() < 1e-10);
    }

    #[test]
    fn test_insufficient_bins() {
        let computer = ValueAreaComputer::new(ValueAreaConfig {
            va_fraction: 0.70,
            min_bins: 20,
        });

        let hist = make_histogram(&[
            (100.0, 100.0),
            (101.0, 100.0),
        ]);

        let va = computer.compute(&hist, 1.0);
        assert!(!va.is_valid);
    }

    #[test]
    fn test_poc_at_edge() {
        let computer = ValueAreaComputer::new(ValueAreaConfig {
            va_fraction: 0.70,
            min_bins: 3,
        });

        // POC at lower edge
        let hist = make_histogram(&[
            (100.0, 200.0), // POC at edge
            (101.0, 50.0),
            (102.0, 50.0),
            (103.0, 50.0),
        ]);

        let va = computer.compute(&hist, 1.0);

        assert!(va.is_valid);
        // Can only expand upward
        assert!((va.val - 100.0).abs() < 1e-10);
    }

    #[test]
    fn test_va_coverage() {
        let computer = ValueAreaComputer::new(ValueAreaConfig {
            va_fraction: 0.70,
            min_bins: 3,
        });

        let hist = make_histogram(&[
            (98.0, 100.0),
            (99.0, 100.0),
            (100.0, 100.0),
            (101.0, 100.0),
            (102.0, 100.0),
        ]);

        let va = computer.compute(&hist, 1.0);

        assert!(va.is_valid);
        // Coverage should be >= 70%
        assert!(va.coverage >= 0.70);
    }

    #[test]
    fn test_empty_histogram() {
        let computer = ValueAreaComputer::new(ValueAreaConfig::default());
        let hist = BTreeMap::new();
        let va = computer.compute(&hist, 1.0);
        assert!(!va.is_valid);
    }
}
