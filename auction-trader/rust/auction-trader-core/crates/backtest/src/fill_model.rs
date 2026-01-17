//! Fill model for backtesting.
//!
//! Models realistic fills using bid/ask prices and slippage.

use auction_core::{Fill, PositionSide, Quote, TimestampMs};

/// Configuration for the fill model.
#[derive(Debug, Clone)]
pub struct FillModelConfig {
    /// Slippage in ticks for entry orders.
    pub slippage_ticks_entry: u32,
    /// Slippage in ticks for exit orders.
    pub slippage_ticks_exit: u32,
    /// Tick size.
    pub tick_size: f64,
    /// Taker fee in basis points.
    pub taker_fee_bps: f64,
    /// Maker fee in basis points (negative = rebate).
    pub maker_fee_bps: f64,
}

impl Default for FillModelConfig {
    fn default() -> Self {
        Self {
            slippage_ticks_entry: 1,
            slippage_ticks_exit: 1,
            tick_size: 0.1,
            taker_fee_bps: 5.0,
            maker_fee_bps: -1.0,
        }
    }
}

/// Fill model for simulating order execution.
pub struct FillModel {
    config: FillModelConfig,
}

impl FillModel {
    /// Create a new fill model.
    pub fn new(config: FillModelConfig) -> Self {
        Self { config }
    }

    /// Simulate a market buy fill.
    pub fn market_buy(&self, ts_ms: TimestampMs, quote: &Quote, size: f64) -> Fill {
        let slippage = self.config.slippage_ticks_entry as f64 * self.config.tick_size;
        let fill_price = quote.ask_px + slippage;
        let notional = fill_price * size;
        let fee = notional * self.config.taker_fee_bps / 10000.0;

        Fill {
            ts_ms,
            price: fill_price,
            size,
            side: PositionSide::Long,
            fee,
            slippage,
        }
    }

    /// Simulate a market sell fill.
    pub fn market_sell(&self, ts_ms: TimestampMs, quote: &Quote, size: f64) -> Fill {
        let slippage = self.config.slippage_ticks_exit as f64 * self.config.tick_size;
        let fill_price = quote.bid_px - slippage;
        let notional = fill_price * size;
        let fee = notional * self.config.taker_fee_bps / 10000.0;

        Fill {
            ts_ms,
            price: fill_price,
            size,
            side: PositionSide::Short,
            fee,
            slippage,
        }
    }

    /// Simulate a limit buy fill (if possible).
    ///
    /// Returns None if the limit price is not hit.
    pub fn limit_buy(
        &self,
        ts_ms: TimestampMs,
        limit_price: f64,
        quote: &Quote,
        size: f64,
    ) -> Option<Fill> {
        // Fill if ask <= limit price
        if quote.ask_px <= limit_price {
            let fill_price = limit_price.min(quote.ask_px);
            let notional = fill_price * size;
            let fee = notional * self.config.maker_fee_bps / 10000.0;

            Some(Fill {
                ts_ms,
                price: fill_price,
                size,
                side: PositionSide::Long,
                fee,
                slippage: 0.0,
            })
        } else {
            None
        }
    }

    /// Simulate a limit sell fill (if possible).
    ///
    /// Returns None if the limit price is not hit.
    pub fn limit_sell(
        &self,
        ts_ms: TimestampMs,
        limit_price: f64,
        quote: &Quote,
        size: f64,
    ) -> Option<Fill> {
        // Fill if bid >= limit price
        if quote.bid_px >= limit_price {
            let fill_price = limit_price.max(quote.bid_px);
            let notional = fill_price * size;
            let fee = notional * self.config.maker_fee_bps / 10000.0;

            Some(Fill {
                ts_ms,
                price: fill_price,
                size,
                side: PositionSide::Short,
                fee,
                slippage: 0.0,
            })
        } else {
            None
        }
    }

    /// Calculate fee for a given notional and order type.
    pub fn calculate_fee(&self, notional: f64, is_maker: bool) -> f64 {
        let bps = if is_maker {
            self.config.maker_fee_bps
        } else {
            self.config.taker_fee_bps
        };
        notional * bps / 10000.0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_quote(bid: f64, ask: f64) -> Quote {
        Quote {
            ts_ms: 0,
            bid_px: bid,
            bid_sz: 100.0,
            ask_px: ask,
            ask_sz: 100.0,
        }
    }

    #[test]
    fn test_market_buy() {
        let model = FillModel::new(FillModelConfig {
            slippage_ticks_entry: 1,
            tick_size: 0.1,
            taker_fee_bps: 5.0,
            ..Default::default()
        });

        let quote = make_quote(50000.0, 50001.0);
        let fill = model.market_buy(1000, &quote, 0.1);

        // Price should be ask + 1 tick slippage
        assert!((fill.price - 50001.1).abs() < 1e-10);
        assert!((fill.slippage - 0.1).abs() < 1e-10);

        // Fee: 50001.1 * 0.1 * 5 / 10000 = 0.25
        assert!((fill.fee - 0.250).abs() < 0.01);
    }

    #[test]
    fn test_market_sell() {
        let model = FillModel::new(FillModelConfig {
            slippage_ticks_exit: 1,
            tick_size: 0.1,
            taker_fee_bps: 5.0,
            ..Default::default()
        });

        let quote = make_quote(50000.0, 50001.0);
        let fill = model.market_sell(1000, &quote, 0.1);

        // Price should be bid - 1 tick slippage
        assert!((fill.price - 49999.9).abs() < 1e-10);
    }

    #[test]
    fn test_limit_buy_filled() {
        let model = FillModel::new(FillModelConfig::default());
        let quote = make_quote(50000.0, 50001.0);

        // Limit at 50002 should fill at 50001 (ask)
        let fill = model.limit_buy(1000, 50002.0, &quote, 0.1);
        assert!(fill.is_some());
        assert!((fill.unwrap().price - 50001.0).abs() < 1e-10);
    }

    #[test]
    fn test_limit_buy_not_filled() {
        let model = FillModel::new(FillModelConfig::default());
        let quote = make_quote(50000.0, 50001.0);

        // Limit at 50000 should not fill (ask is 50001)
        let fill = model.limit_buy(1000, 50000.0, &quote, 0.1);
        assert!(fill.is_none());
    }

    #[test]
    fn test_maker_rebate() {
        let model = FillModel::new(FillModelConfig {
            maker_fee_bps: -1.0, // Rebate
            ..Default::default()
        });

        let fee = model.calculate_fee(10000.0, true);
        assert!((fee - (-1.0)).abs() < 1e-10); // -1.0 = 10000 * -1 / 10000
    }
}
