# Claude Onboarding: auction-trader

This document provides everything Claude needs to work effectively on the auction-trader project.

---

## Project Overview

**auction-trader** is a systematic BTC perpetual futures trading system implementing Auction Market Theory (AMT) mechanics on Bybit.

### Core Concept
Markets alternate between **balance** (value acceptance) and **imbalance** (price discovery). We trade three high-probability setups:

1. **Break-in** — Failed auction returning to value (mean reversion)
2. **Breakout** — Acceptance outside value (trend continuation)
3. **Failed breakout** — Fakeout reversal back into value

### Tech Stack
- **Python + Rust hybrid** with PyO3 bindings
- **Rust:** Data ingestion, feature computation, backtesting (hot paths)
- **Python:** Signal engine, execution, orchestration
- **Storage:** DuckDB (raw/features) + SQLite (signals/execution)
- **Dashboard:** FastAPI + TradeCore's footprint charts

---

## Key Files

### Documentation
| File | Purpose |
|------|---------|
| `specv2.md` | Complete technical specification (22 sections) |
| `PROJECT_INTENT.md` | Project background + all design decisions Q&A |
| `claude.md` | This file - Claude onboarding |
| `SPEC.md` | Original spec (superseded by specv2.md) |
| `PRD.md` | Original PRD (superseded by specv2.md) |

### Configuration
| File | Purpose |
|------|---------|
| `config/default.yaml` | Default trading parameters |
| `config/.env` | Secrets (API keys, webhook URLs) |

### Rust Core (when created)
```
rust/auction-trader-core/
├── crates/core/       # Shared types, config
├── crates/ingestion/  # Trade/quote normalization
├── crates/features/   # VA, OF, sigma computation
└── crates/backtest/   # Simulation engine with bid/ask fills
```

### Python Components (when created)
```
python/auction_trader/
├── collector.py       # WebSocket data ingestion
├── signal_engine.py   # Setup evaluation, state machine
├── execution.py       # Order management, risk checks
└── dashboard/         # FastAPI web interface
```

### Databases
| Database | Engine | Contents |
|----------|--------|----------|
| `data/raw.duckdb` | DuckDB | Trades, quotes (48h rolling) |
| `data/features.duckdb` | DuckDB | 1m bars, features (permanent) |
| `data/signals.db` | SQLite | Signal state, acceptance counters |
| `data/execution.db` | SQLite | Positions, orders, P&L, snapshots |

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        AUCTION-TRADER                            │
├─────────────────────────────────────────────────────────────────┤
│  Collector (Python WS) → Normalizer (Rust) → Feature Engine (Rust)
│         ↓                                           ↓
│    raw.duckdb                               features.duckdb
│                                                     ↓
│  Signal Engine (Python) ← signals.db ← Backtester (Rust)
│         ↓
│  Execution Engine (Python) → execution.db → Web Dashboard
└─────────────────────────────────────────────────────────────────┘
```

---

## Critical Design Decisions

### Signal Logic
- **Priority:** Break-in > Failed breakout > Breakout
- **Acceptance:** 3 consecutive closes outside VA (hard reset on 1 inside close)
- **VA boundaries:** Lock original VAH/VAL at sequence start
- **Gap handling:** Require actual crossing (trade within 1x spread of boundary)

### Order Flow
- **High ambiguous fraction (>35%):** Require BOTH OF AND qimb confirmation
- **Tick rule fallback:** Zero-tick continuation (use previous trade's sign)
- **Aggregate same-timestamp trades** to avoid split-trade double-counting

### Value Area
- **Strictly contiguous** (include zero-volume bins between populated bins)
- **Minimum 20 bins** required for valid VA
- **Rebuild from raw trades** when bin_width changes >25%

### Position Sizing
- **Formula:** `position = (available_margin * 2%) / (entry - stop)`
- **Max leverage:** 10x (reduce position to fit if exceeded)
- **Partial exits:** 30% at TP1, 70% at TP2
- **After TP1:** Move stop to breakeven

### Risk Management
- **Cooldown:** Global (no trades either direction) for 3 minutes after exit
- **Time stop:** 60 min base, trail while profitable (net of fees)
- **Kill switch:** UTC calendar day, includes funding in P&L

### Execution
- **Entry orders:** Limit (convert to market after 1 minute)
- **Exit orders:** Market always
- **Position mismatch:** Alert and halt (no auto-reconciliation)

### Modes
- **dry_run:** Signals generated, no execution
- **paper:** Simulated fills (same model as backtest)
- **shadow:** Live signals + paper trades, alert on divergence
- **live:** Real execution (requires manual activation)

---

## Coding Standards

### Rust
- Use `Result<T, E>` for fallible operations
- Prefer iterators over explicit loops
- Document public APIs with `///` comments
- Use `#[derive(Debug, Clone)]` liberally
- Error types should implement `std::error::Error`

### Python
- Type hints on all function signatures
- Docstrings for public functions (Google style)
- Use `async/await` for I/O operations
- Pydantic models for configuration and data transfer
- pytest for testing

### General
- Keep functions focused and small
- Prefer composition over inheritance
- Write tests for critical calculations
- Log at appropriate levels (debug for flow, info for events, error for failures)

---

## Testing Requirements

### Unit Tests (Comprehensive)
- **Rust:** All feature computations, VA algorithms, trade inference
- **Python:** Signal engine logic, state machine transitions, sizing calculations

### Integration Tests
- End-to-end signal generation from raw tick data
- Deterministic replay (same inputs = same outputs)
- No look-ahead verification

### What to Test
- Boundary conditions (exactly at VAH/VAL)
- Edge cases (empty histogram, single bin, gaps)
- Numeric precision (floating point comparisons)

---

## Common Workflows

### Running the System
```bash
# Development
cd /home/soka/Desktop/exp1/auction-trader
python -m auction_trader.main --mode dry_run

# Paper trading
python -m auction_trader.main --mode paper

# Live (requires manual activation in dashboard)
python -m auction_trader.main --mode live
```

### Running Backtests
```bash
# Single backtest
python -m auction_trader.backtest --config config/default.yaml

# Grid search
python -m auction_trader.backtest --grid --params alpha_bin:0.2,0.25,0.3
```

### Building Rust Components
```bash
cd rust/auction-trader-core
cargo build --release
maturin develop  # For PyO3 bindings
```

### Running Tests
```bash
# Rust tests
cd rust/auction-trader-core
cargo test

# Python tests
pytest python/tests/
```

---

## External Codebases to Reference

### TradeCore (for charts)
- **Location:** `/home/soka/Desktop/TradeCore`
- **Use:** `frontend/total-core-v2.js` for footprint visualization
- **Key function:** `build_candles_with_profiles()` in `app/ingestion/trades.py`

### Total-Trader (for WebSocket)
- **Location:** `/home/soka/Desktop/Total-Trader-v0.11`
- **Use:** `backend/app/services/websocket_service.py` for Bybit WS
- **Use:** `backend/app/services/exchange_service.py` for API patterns

---

## Key Numbers to Remember

| Parameter | Value | Notes |
|-----------|-------|-------|
| Rolling window | 240 min | 4 hours |
| VA fraction | 70% | Volume contained in VA |
| Min VA bins | 20 | Below this, VA invalid |
| Acceptance k | 3 | Consecutive closes for breakout |
| Risk per trade | 2% | Of available margin |
| Max leverage | 10x | Bybit cross margin limit |
| TP1 allocation | 30% | Partial exit |
| TP2 allocation | 70% | Remaining position |
| Cooldown | 3 min | After any exit |
| Max hold | 60 min | Base, extends if profitable |
| Quote staleness | 250 ms | Max for trade classification |
| Ambiguous threshold | 35% | Triggers dual confirmation |

---

## Gotchas and Pitfalls

1. **VA boundaries shift** — Lock VAH/VAL at sequence start for acceptance counting
2. **Split trades** — Exchange may report one order as multiple fills; aggregate by timestamp
3. **Gaps** — Price can gap through VA; require actual crossing for validity
4. **Stale quotes** — Use them anyway (better than skipping); track staleness
5. **1 contract positions** — Skip partial exits; full exit at TP2 only
6. **Leverage cap** — Reduce position size, don't reject trade
7. **Timer start** — Begins at bar close AFTER fill, not at signal time

---

## When in Doubt

1. **Check specv2.md** — It has the authoritative specification
2. **Check PROJECT_INTENT.md** — It has the rationale for decisions
3. **Prioritize simplicity** — v0 is about getting it working correctly
4. **Don't over-engineer** — Post-v0 enhancements are documented separately
5. **Test critical calculations** — Especially VA, OF, and position sizing

---

## Project Status

**Current Phase:** Initial setup and documentation

**Completed:**
- [x] Comprehensive spec interview (70+ decisions)
- [x] specv2.md created
- [x] claude.md created

**In Progress:**
- [ ] PROJECT_INTENT.md
- [ ] Git initialization
- [ ] Project structure setup

**Pending:**
- [ ] Rust core implementation
- [ ] Python orchestration
- [ ] Dashboard integration
- [ ] Backtesting validation
- [ ] Paper trading
- [ ] Live deployment
