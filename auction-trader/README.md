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

## Current Status

**Phase:** Implementation Complete - Testing in Progress
**Session:** 3
**Latest:** Dashboard with Terminal Noir aesthetic, 66 Python tests passing

### What Works Now
- ✅ Python package with signal engine, position manager, orchestrator
- ✅ Rust core crates (ingestion, features, backtest) - code complete
- ✅ Web dashboard with real-time updates (mock data)
- ✅ 66 Python unit tests (100% passing)
- ⏳ Rust unit tests (next priority)
- ⏳ Integration with live Bybit data (ready, untested)

See [HANDOFF.md](HANDOFF.md) for detailed session history and next steps.

## Quick Start

### Prerequisites

- Python 3.10+
- Rust 1.70+ (for building PyO3 bindings)
- Git

### Installation

1. Clone the repository:
```bash
git clone https://github.com/Sokalledcoder/Auction-trader.git
cd auction-trader
```

2. Create and activate virtual environment:
```bash
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate  # Windows
```

3. Install dependencies:
```bash
pip install -e .
```

### Running the Dashboard

```bash
# Start the web dashboard
auction-trader dashboard

# Or run directly
cd dashboard && uvicorn api:app --reload
```

Visit http://127.0.0.1:8000 to see the Terminal Noir themed dashboard.

### Running Tests

```bash
# All Python tests
pytest tests/ -v

# With coverage
pytest tests/ --cov=auction_trader

# Specific test file
pytest tests/test_signal_engine.py -v
```

### Building Rust Components

```bash
cd rust/auction-trader-core
cargo build --release
cargo test  # After Rust tests are written
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
├── README.md              # This file
├── HANDOFF.md             # Detailed session history and next steps
├── pyproject.toml         # Python dependencies
├── .gitignore
│
├── dashboard/             # Web interface
│   ├── api.py            # FastAPI backend with WebSocket
│   └── static/
│       ├── index.html    # Dashboard UI (Terminal Noir theme)
│       ├── style.css     # Custom styling
│       └── app.js        # Chart.js + real-time updates
│
├── python/
│   └── auction_trader/
│       ├── cli.py             # Command-line interface
│       ├── config.py          # Configuration management
│       ├── models/
│       │   └── types.py       # Core data models
│       └── services/
│           ├── collector.py       # WebSocket data collection
│           ├── signal_engine.py   # Signal generation
│           ├── position_manager.py # Risk and position management
│           └── orchestrator.py    # System coordination
│
├── rust/
│   └── auction-trader-core/
│       └── crates/
│           ├── core/         # Shared types and config
│           ├── ingestion/    # Trade/quote normalization
│           ├── features/     # VA and order flow computation
│           └── backtest/     # Simulation engine
│
└── tests/                 # Python test suite (66 tests)
    ├── conftest.py       # Pytest fixtures
    ├── test_types.py
    ├── test_config.py
    ├── test_signal_engine.py
    └── test_position_manager.py
```

**Key Documentation:**
- `/home/soka/Desktop/exp1/specv2.md` - Full technical specification
- `/home/soka/Desktop/exp1/claude.md` - Claude onboarding guide
- `/home/soka/Desktop/exp1/PROJECT_INTENT.md` - Design decisions Q&A

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
