# Auction Trader

A systematic trading system implementing Auction Market Theory (AMT) mechanics on BTC perpetuals via Bybit.

## Overview

Auction Trader uses Value Area analysis and order flow to identify high-probability trading setups:

- **Break-in**: Failed auction returning to value (mean reversion)
- **Breakout**: Acceptance outside value area (trend continuation)
- **Failed Breakout**: Fakeout reversal back into value

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Python Layer                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐ │
│  │ Collector│  │  Signal  │  │ Position │  │    Execution     │ │
│  │(WebSocket│  │  Engine  │  │ Manager  │  │     Engine       │ │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────────┬─────────┘ │
│       │             │             │                  │           │
│       └─────────────┴─────────────┴──────────────────┘           │
│                            │                                     │
│  ┌─────────────────────────┴─────────────────────────────────┐  │
│  │                    Orchestrator                            │  │
│  └─────────────────────────┬─────────────────────────────────┘  │
└────────────────────────────┼─────────────────────────────────────┘
                             │ PyO3
┌────────────────────────────┼─────────────────────────────────────┐
│                        Rust Layer                                │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐ │
│  │   Core   │  │Ingestion │  │ Features │  │    Backtest      │ │
│  │  Types   │  │Classifier│  │VA/OF/Vol │  │   Simulator      │ │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

## Quick Start

### Prerequisites

- Python 3.10+
- Rust 1.70+ (for building the native extension)
- Bybit API credentials (for live/paper trading)

### Installation

1. Clone the repository:
```bash
git clone https://github.com/user/auction-trader.git
cd auction-trader
```

2. Create a virtual environment:
```bash
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# or
.venv\Scripts\activate  # Windows
```

3. Install with maturin (builds Rust extension):
```bash
pip install maturin
maturin develop
```

4. Copy and configure environment:
```bash
cp config/.env.example .env
# Edit .env with your Bybit API credentials
```

### Running

**Paper Trading** (simulated with real data):
```bash
auction-trader run --mode paper
```

**Live Trading** (real money - use with caution!):
```bash
export BYBIT_API_KEY=your_key
export BYBIT_API_SECRET=your_secret
export BYBIT_TESTNET=false
auction-trader run --mode live --confirm-live
```

**Shadow Mode** (track signals without executing):
```bash
auction-trader run --mode shadow
```

### Configuration

Default configuration is in `config/default.yaml`. Key parameters:

```yaml
instrument:
  symbol: BTCUSDT
  exchange: bybit
  rolling_window_minutes: 240  # 4-hour rolling window

value_area:
  va_fraction: 0.70  # 70% of volume for VA
  min_va_bins: 20    # Minimum bins for valid VA

signal:
  accept_outside_k: 3  # Bars needed for breakout acceptance

sizing:
  risk_pct: 0.02      # 2% risk per trade
  max_leverage: 10.0  # Maximum leverage
  tp1_pct: 0.30       # 30% exit at TP1
  tp2_pct: 0.70       # 70% exit at TP2
```

## Project Structure

```
auction-trader/
├── config/
│   ├── default.yaml      # Default configuration
│   └── .env.example      # Environment template
├── python/
│   └── auction_trader/
│       ├── models/       # Data types
│       ├── services/     # Core services
│       ├── storage/      # Database layer
│       ├── orchestrator.py
│       └── cli.py
├── rust/
│   └── auction-trader-core/
│       └── crates/
│           ├── core/      # Shared types
│           ├── ingestion/ # Trade classification
│           ├── features/  # VA, OF computation
│           ├── backtest/  # Simulation engine
│           └── pyo3_bindings/  # Python bindings
├── data/                  # Database files (gitignored)
├── logs/                  # Log files (gitignored)
└── pyproject.toml
```

## Trading Logic

### Signal Priority

When multiple signals are possible, priority determines which executes:

1. **Break-in** (highest) - Mean reversion has highest win rate
2. **Failed Breakout** - Fakeout reversals
3. **Breakout** (lowest) - Trend continuation

### Position Management

- **Entry**: Limit orders with 1-minute timeout, fallback to market
- **TP1**: Close 30% at POC, move stop to breakeven
- **TP2**: Close remaining 70% at opposite VA boundary
- **Stop Loss**: Set at VA boundary + buffer ticks
- **Time Stop**: 60-minute max hold, extended if profitable

### Order Flow Confirmation

Signals require order flow confirmation:
- **Entry**: OF or normalized OF above threshold
- **Breakout**: Sustained buying/selling pressure
- **Failed Breakout**: Reversal in order flow direction

## Development

### Running Tests

```bash
pytest tests/
```

### Type Checking

```bash
mypy python/auction_trader/
```

### Formatting

```bash
black python/
ruff check python/
```

### Building Rust Components

```bash
cd rust/auction-trader-core
cargo build --release
```

## License

MIT License - See LICENSE file for details.

## Disclaimer

This software is for educational purposes only. Trading cryptocurrencies involves substantial risk of loss. Past performance does not guarantee future results. Use at your own risk.
