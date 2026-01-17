//! Backtest performance metrics.
//!
//! Calculates various performance metrics from backtest results.

use crate::position::ClosedTrade;

/// Backtest performance metrics.
#[derive(Debug, Clone, Default)]
pub struct BacktestMetrics {
    /// Total number of trades.
    pub total_trades: u32,
    /// Number of winning trades.
    pub winning_trades: u32,
    /// Number of losing trades.
    pub losing_trades: u32,
    /// Win rate (0-1).
    pub win_rate: f64,
    /// Gross P&L (before fees).
    pub gross_pnl: f64,
    /// Net P&L (after fees and funding).
    pub net_pnl: f64,
    /// Total fees paid.
    pub total_fees: f64,
    /// Total funding paid.
    pub total_funding: f64,
    /// Average winning trade P&L.
    pub avg_win: f64,
    /// Average losing trade P&L.
    pub avg_loss: f64,
    /// Profit factor (gross wins / gross losses).
    pub profit_factor: f64,
    /// Maximum drawdown (absolute).
    pub max_drawdown: f64,
    /// Maximum drawdown percentage.
    pub max_drawdown_pct: f64,
    /// Sharpe ratio (annualized, assuming 1-min bars).
    pub sharpe_ratio: f64,
    /// Sortino ratio.
    pub sortino_ratio: f64,
    /// Total return percentage.
    pub total_return_pct: f64,
    /// Average trade duration in minutes.
    pub avg_trade_duration_min: f64,
    /// Largest winning trade.
    pub largest_win: f64,
    /// Largest losing trade.
    pub largest_loss: f64,
    /// Consecutive wins (max).
    pub max_consecutive_wins: u32,
    /// Consecutive losses (max).
    pub max_consecutive_losses: u32,
}

/// Equity curve point.
#[derive(Debug, Clone)]
pub struct EquityPoint {
    pub ts_ms: i64,
    pub equity: f64,
    pub drawdown: f64,
    pub drawdown_pct: f64,
}

/// Metrics calculator.
pub struct MetricsCalculator {
    initial_capital: f64,
}

impl MetricsCalculator {
    /// Create a new metrics calculator.
    pub fn new(initial_capital: f64) -> Self {
        Self { initial_capital }
    }

    /// Calculate metrics from closed trades.
    pub fn calculate(&self, trades: &[ClosedTrade]) -> BacktestMetrics {
        if trades.is_empty() {
            return BacktestMetrics::default();
        }

        let mut metrics = BacktestMetrics::default();

        // Basic counts
        metrics.total_trades = trades.len() as u32;

        let mut gross_wins = 0.0;
        let mut gross_losses = 0.0;
        let mut total_win_pnl = 0.0;
        let mut total_loss_pnl = 0.0;
        let mut total_duration = 0i64;

        // Consecutive tracking
        let mut current_wins = 0u32;
        let mut current_losses = 0u32;

        for trade in trades {
            metrics.net_pnl += trade.pnl;
            metrics.total_fees += trade.fees;
            metrics.total_funding += trade.funding;

            let gross = trade.pnl + trade.fees + trade.funding;
            metrics.gross_pnl += gross;

            total_duration += trade.exit_ts - trade.entry_ts;

            if trade.pnl > 0.0 {
                metrics.winning_trades += 1;
                total_win_pnl += trade.pnl;
                gross_wins += gross;
                metrics.largest_win = metrics.largest_win.max(trade.pnl);

                current_wins += 1;
                current_losses = 0;
                metrics.max_consecutive_wins = metrics.max_consecutive_wins.max(current_wins);
            } else {
                metrics.losing_trades += 1;
                total_loss_pnl += trade.pnl;
                gross_losses += gross.abs();
                metrics.largest_loss = metrics.largest_loss.min(trade.pnl);

                current_losses += 1;
                current_wins = 0;
                metrics.max_consecutive_losses = metrics.max_consecutive_losses.max(current_losses);
            }
        }

        // Averages
        metrics.win_rate = if metrics.total_trades > 0 {
            metrics.winning_trades as f64 / metrics.total_trades as f64
        } else {
            0.0
        };

        metrics.avg_win = if metrics.winning_trades > 0 {
            total_win_pnl / metrics.winning_trades as f64
        } else {
            0.0
        };

        metrics.avg_loss = if metrics.losing_trades > 0 {
            total_loss_pnl / metrics.losing_trades as f64
        } else {
            0.0
        };

        metrics.profit_factor = if gross_losses > 0.0 {
            gross_wins / gross_losses
        } else if gross_wins > 0.0 {
            f64::INFINITY
        } else {
            0.0
        };

        metrics.avg_trade_duration_min = if metrics.total_trades > 0 {
            (total_duration as f64 / metrics.total_trades as f64) / 60_000.0
        } else {
            0.0
        };

        // Total return
        metrics.total_return_pct = (metrics.net_pnl / self.initial_capital) * 100.0;

        // Calculate drawdown and Sharpe from equity curve
        let equity_curve = self.build_equity_curve(trades);
        if !equity_curve.is_empty() {
            // Max drawdown
            for point in &equity_curve {
                if point.drawdown > metrics.max_drawdown {
                    metrics.max_drawdown = point.drawdown;
                    metrics.max_drawdown_pct = point.drawdown_pct;
                }
            }

            // Sharpe ratio (simplified - using trade returns)
            let returns: Vec<f64> = trades.iter().map(|t| t.pnl / self.initial_capital).collect();
            metrics.sharpe_ratio = self.calculate_sharpe(&returns);
            metrics.sortino_ratio = self.calculate_sortino(&returns);
        }

        metrics
    }

    /// Build equity curve from trades.
    pub fn build_equity_curve(&self, trades: &[ClosedTrade]) -> Vec<EquityPoint> {
        let mut curve = Vec::with_capacity(trades.len() + 1);

        // Starting point
        curve.push(EquityPoint {
            ts_ms: 0,
            equity: self.initial_capital,
            drawdown: 0.0,
            drawdown_pct: 0.0,
        });

        let mut equity = self.initial_capital;
        let mut peak = self.initial_capital;

        for trade in trades {
            equity += trade.pnl;
            peak = peak.max(equity);

            let drawdown = peak - equity;
            let drawdown_pct = if peak > 0.0 {
                (drawdown / peak) * 100.0
            } else {
                0.0
            };

            curve.push(EquityPoint {
                ts_ms: trade.exit_ts,
                equity,
                drawdown,
                drawdown_pct,
            });
        }

        curve
    }

    /// Calculate Sharpe ratio from returns.
    fn calculate_sharpe(&self, returns: &[f64]) -> f64 {
        if returns.len() < 2 {
            return 0.0;
        }

        let n = returns.len() as f64;
        let mean = returns.iter().sum::<f64>() / n;
        let variance = returns.iter().map(|r| (r - mean).powi(2)).sum::<f64>() / n;
        let std_dev = variance.sqrt();

        if std_dev > 0.0 {
            // Annualize: assume 525600 minutes per year, each trade is roughly independent
            // Simplified: just scale by sqrt of trades per year estimate
            let annualization = (252.0 * 24.0 * 60.0 / n.max(1.0)).sqrt();
            (mean / std_dev) * annualization
        } else {
            0.0
        }
    }

    /// Calculate Sortino ratio from returns.
    fn calculate_sortino(&self, returns: &[f64]) -> f64 {
        if returns.len() < 2 {
            return 0.0;
        }

        let n = returns.len() as f64;
        let mean = returns.iter().sum::<f64>() / n;

        // Downside deviation (only negative returns)
        let downside_variance = returns
            .iter()
            .filter(|&&r| r < 0.0)
            .map(|r| r.powi(2))
            .sum::<f64>()
            / n;
        let downside_dev = downside_variance.sqrt();

        if downside_dev > 0.0 {
            let annualization = (252.0 * 24.0 * 60.0 / n.max(1.0)).sqrt();
            (mean / downside_dev) * annualization
        } else if mean > 0.0 {
            f64::INFINITY
        } else {
            0.0
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::position::ExitReason;
    use auction_core::PositionSide;

    fn make_trade(pnl: f64, fees: f64, duration_ms: i64) -> ClosedTrade {
        ClosedTrade {
            entry_ts: 0,
            exit_ts: duration_ms,
            side: PositionSide::Long,
            entry_price: 50000.0,
            exit_price: 50000.0 + pnl * 10.0,
            size: 0.1,
            pnl,
            fees,
            funding: 0.0,
            exit_reason: ExitReason::TakeProfit1,
            strategy_tag: "test".to_string(),
        }
    }

    #[test]
    fn test_basic_metrics() {
        let calculator = MetricsCalculator::new(10000.0);

        let trades = vec![
            make_trade(100.0, 5.0, 60_000),  // Win
            make_trade(-50.0, 5.0, 120_000), // Loss
            make_trade(75.0, 5.0, 90_000),   // Win
        ];

        let metrics = calculator.calculate(&trades);

        assert_eq!(metrics.total_trades, 3);
        assert_eq!(metrics.winning_trades, 2);
        assert_eq!(metrics.losing_trades, 1);
        assert!((metrics.win_rate - 0.6667).abs() < 0.01);
        assert!((metrics.net_pnl - 125.0).abs() < 1e-10); // 100 - 50 + 75
    }

    #[test]
    fn test_empty_trades() {
        let calculator = MetricsCalculator::new(10000.0);
        let metrics = calculator.calculate(&[]);

        assert_eq!(metrics.total_trades, 0);
        assert_eq!(metrics.net_pnl, 0.0);
    }

    #[test]
    fn test_equity_curve() {
        let calculator = MetricsCalculator::new(10000.0);

        let trades = vec![
            make_trade(100.0, 0.0, 60_000),
            make_trade(-150.0, 0.0, 120_000), // Creates drawdown
            make_trade(200.0, 0.0, 180_000),
        ];

        let curve = calculator.build_equity_curve(&trades);

        assert_eq!(curve.len(), 4); // Initial + 3 trades
        assert!((curve[0].equity - 10000.0).abs() < 1e-10);
        assert!((curve[1].equity - 10100.0).abs() < 1e-10);
        assert!((curve[2].equity - 9950.0).abs() < 1e-10);
        assert!(curve[2].drawdown > 0.0); // Should have drawdown
    }

    #[test]
    fn test_consecutive_wins_losses() {
        let calculator = MetricsCalculator::new(10000.0);

        let trades = vec![
            make_trade(10.0, 0.0, 1000),
            make_trade(10.0, 0.0, 2000),
            make_trade(10.0, 0.0, 3000), // 3 consecutive wins
            make_trade(-5.0, 0.0, 4000),
            make_trade(-5.0, 0.0, 5000), // 2 consecutive losses
        ];

        let metrics = calculator.calculate(&trades);

        assert_eq!(metrics.max_consecutive_wins, 3);
        assert_eq!(metrics.max_consecutive_losses, 2);
    }
}
