# specv2.md — BTC Perps "PBD / Auction Market Theory" System (Bybit)

**Spec version:** 2.0
**Project name:** `auction-trader`
**Instrument:** BTC perpetual (Bybit Unified Trading v5 API)
**Mode:** Directional, risk-based position sizing, leverage up to 10x
**Primary TF:** 1-minute
**Core window ("rolling session"):** 4 hours (240 minutes)
**Tech stack:** Python + Rust hybrid (PyO3 bindings)

---

## 0) Executive Summary

### What this system does
A systematic trading system implementing Auction Market Theory (AMT) mechanics on BTC perpetuals. It identifies three high-probability setups:
1. **Break-in** — Failed auction returning to value (mean reversion)
2. **Breakout** — Acceptance outside value (trend continuation)
3. **Failed breakout** — Fakeout reversal back into value

### v0 Delivers
1. Data collection via Bybit WebSocket (Python initially, Rust migration later)
2. Rolling 4h value area engine (VAH/VAL/POC) with volatility-scaled bins (Rust)
3. Initiative metrics: inferred signed order flow + quote imbalance (Rust)
4. Signal engine for 3 setups with configurable parameters (Python)
5. Backtester with bid/ask fill modeling (Rust, new implementation)
6. Risk-based position sizing with leverage support
7. Web dashboard with full trading view + footprint charts (reuse TradeCore)
8. Grid search across all configurable parameters
9. Paper trading, shadow mode, and dry-run modes
10. Live trading with manual activation

### v0 Does NOT Deliver
- Full P/B/D shape classification (profile-shape recognition)
- Multi-asset / portfolio / hedged spreads
- ML/CPO parameter selection
- Sub-second execution optimization / HFT infrastructure
- Multi-strategy parallel execution (post-v0)

---

## 1) Glossary

| Term | Definition |
|------|------------|
| **VA (Value Area)** | Price region where the market "accepted" trade (high time/volume) |
| **POC** | Point of Control; price/bin with maximum volume in the profile |
| **VAH / VAL** | Value Area High/Low boundaries containing ~70% of volume |
| **Acceptance** | k consecutive closes outside VA boundary (k=3 default) |
| **Initiative** | Aggressive crossing approximated by signed order flow |
| **OF_1m** | Inferred signed order flow over 1 minute |
| **OF_norm_1m** | Normalized order flow: OF_1m / total_volume |
| **qimb** | Quote imbalance: (bid_size − ask_size) / (bid_size + ask_size) |
| **BVC** | Bulk Volume Classification proxy (manual fallback only) |
| **Heat** | Current risk exposure as % of capital |

---

## 2) System Architecture

### 2.1 High-Level Components

```
┌─────────────────────────────────────────────────────────────────┐
│                        AUCTION-TRADER                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │
│  │  COLLECTOR   │    │  NORMALIZER  │    │   FEATURE    │       │
│  │  (Python WS) │───▶│    (Rust)    │───▶│   ENGINE     │       │
│  │              │    │              │    │   (Rust)     │       │
│  └──────────────┘    └──────────────┘    └──────────────┘       │
│         │                                       │                │
│         ▼                                       ▼                │
│  ┌──────────────┐                       ┌──────────────┐        │
│  │   RAW DB     │                       │ FEATURES DB  │        │
│  │  (DuckDB)    │                       │  (DuckDB)    │        │
│  └──────────────┘                       └──────────────┘        │
│                                                │                 │
│                                                ▼                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │
│  │   SIGNAL     │◀───│   SIGNALS    │◀───│  BACKTESTER  │       │
│  │   ENGINE     │    │     DB       │    │   (Rust)     │       │
│  │  (Python)    │    │  (SQLite)    │    │              │       │
│  └──────────────┘    └──────────────┘    └──────────────┘       │
│         │                                                        │
│         ▼                                                        │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │
│  │  EXECUTION   │───▶│ EXECUTION DB │    │    WEB       │       │
│  │   ENGINE     │    │  (SQLite)    │    │  DASHBOARD   │       │
│  │  (Python)    │    │              │    │  (FastAPI)   │       │
│  └──────────────┘    └──────────────┘    └──────────────┘       │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 Component Responsibilities

| Component | Language | Responsibility |
|-----------|----------|----------------|
| **Collector** | Python | WebSocket ingestion from Bybit (trades, L1 quotes) |
| **Normalizer** | Rust | Time alignment, trade/quote matching, minute bucketing |
| **Feature Engine** | Rust | Rolling histogram, VA computation, OF/qimb calculation |
| **Signal Engine** | Python | Setup evaluation, state machine, intent generation |
| **Backtester** | Rust | Tick replay with bid/ask fill model |
| **Execution Engine** | Python | Order placement, position management, risk checks |
| **Web Dashboard** | FastAPI + JS | Real-time visualization, grid search results |

### 2.3 Database Architecture (4 DBs in single directory)

| Database | Engine | Purpose | Retention |
|----------|--------|---------|-----------|
| `raw.duckdb` | DuckDB | Trades, quotes (tick data) | 48h rolling (batched daily purge) |
| `features.duckdb` | DuckDB | 1m bars, features, indicators | Indefinite |
| `signals.db` | SQLite | Signal state, acceptance counters | Indefinite |
| `execution.db` | SQLite | Positions, orders, P&L, state snapshots | Indefinite |

### 2.4 Runtime Cadence

- **Data ingestion:** Continuous via WebSocket
- **Feature updates:** Every minute at bar close
- **Signal evaluation:** At minute close (bar finalization)
- **Order submission:** Immediately after signal evaluation
- **State snapshots:** Every 5 minutes + on state change
- **Fill model:** First quote after decision timestamp

---

## 3) Data Requirements

### 3.1 Live Inputs

**Trades (prints)**
```
ts_ms: int64        # Timestamp in milliseconds
price: float64      # Trade price
size: float64       # Trade size (contracts/BTC)
```

**L1 Quotes**
```
ts_ms: int64        # Timestamp in milliseconds
bid_px: float64     # Best bid price
bid_sz: float64     # Best bid size
ask_px: float64     # Best ask price
ask_sz: float64     # Best ask size
```

### 3.2 Historical Data

- **Source:** Downloaded/purchased CSV files
- **Format converter:** Auto-detect timestamp format with best-effort fallback
- **Supported formats:** Unix ms, Unix seconds, ISO 8601

### 3.3 Storage Schema

#### Table: `raw_trades` (DuckDB)
```sql
CREATE TABLE raw_trades (
    ts_ms       BIGINT NOT NULL,
    price       DOUBLE NOT NULL,
    size        DOUBLE NOT NULL,
    inferred_side TINYINT,      -- +1=buy, -1=sell, 0=ambiguous
    quote_bid_px DOUBLE,
    quote_ask_px DOUBLE,
    quote_staleness_ms INT
);
```

#### Table: `raw_quotes` (DuckDB)
```sql
CREATE TABLE raw_quotes (
    ts_ms    BIGINT NOT NULL,
    bid_px   DOUBLE NOT NULL,
    bid_sz   DOUBLE NOT NULL,
    ask_px   DOUBLE NOT NULL,
    ask_sz   DOUBLE NOT NULL
);
```

#### Table: `bars_1m` (DuckDB)
```sql
CREATE TABLE bars_1m (
    ts_min          BIGINT PRIMARY KEY,  -- Unix minute boundary UTC
    open            DOUBLE,
    high            DOUBLE,
    low             DOUBLE,
    close           DOUBLE,
    volume          DOUBLE,
    vwap            DOUBLE,
    trade_count     INT,
    bid_px_close    DOUBLE,
    ask_px_close    DOUBLE,
    bid_sz_close    DOUBLE,
    ask_sz_close    DOUBLE,
    spread_close    DOUBLE,
    spread_avg_60m  DOUBLE  -- Rolling 60-min average spread
);
```

#### Table: `features_1m` (DuckDB)
```sql
CREATE TABLE features_1m (
    ts_min                  BIGINT PRIMARY KEY,
    mid_close               DOUBLE,
    sigma_240               DOUBLE,
    bin_width               DOUBLE,
    poc                     DOUBLE,
    vah                     DOUBLE,
    val                     DOUBLE,
    va_coverage             DOUBLE,
    va_bin_count            INT,
    of_1m                   DOUBLE,
    of_norm_1m              DOUBLE,
    qimb_close              DOUBLE,
    qimb_ema_60s            DOUBLE,
    ambiguous_trade_frac_1m DOUBLE,
    total_volume_4h         DOUBLE
);
```

#### Table: `signals_1m` (SQLite)
```sql
CREATE TABLE signals_1m (
    ts_min                  INTEGER PRIMARY KEY,
    signal_breakin_long     INTEGER,  -- 0/1
    signal_breakin_short    INTEGER,
    signal_breakout_long    INTEGER,
    signal_breakout_short   INTEGER,
    signal_failbreak_long   INTEGER,
    signal_failbreak_short  INTEGER,
    acceptance_state        TEXT,     -- JSON: counters, locked VAH/VAL
    chosen_action           TEXT,     -- ENTER_LONG, ENTER_SHORT, EXIT, HOLD
    reason_code             TEXT,
    feature_snapshot        TEXT      -- JSON: VA values at decision time
);
```

#### Table: `positions` (SQLite)
```sql
CREATE TABLE positions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms_open          INTEGER NOT NULL,
    ts_ms_close         INTEGER,
    side                TEXT NOT NULL,      -- LONG/SHORT
    entry_price         DOUBLE NOT NULL,
    exit_price          DOUBLE,
    qty_contracts       DOUBLE NOT NULL,
    qty_at_tp1          DOUBLE,             -- Partial exit quantity
    stop_price          DOUBLE NOT NULL,
    tp1_price           DOUBLE,
    tp2_price           DOUBLE,
    strategy_tag        TEXT,               -- breakin/breakout/failbreak
    status              TEXT,               -- OPEN/CLOSED/STOPPED
    realized_pnl        DOUBLE,
    fees_paid           DOUBLE,
    funding_paid        DOUBLE,
    leverage_used       DOUBLE,
    risk_pct_used       DOUBLE
);
```

#### Table: `state_snapshots` (SQLite)
```sql
CREATE TABLE state_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms           INTEGER NOT NULL,
    snapshot_type   TEXT,           -- PERIODIC/STATE_CHANGE
    position_state  TEXT,           -- JSON
    acceptance_state TEXT,          -- JSON
    pending_orders  TEXT,           -- JSON
    account_state   TEXT            -- JSON: equity, margin, heat
);
```

#### Table: `grid_search_results` (SQLite)
```sql
CREATE TABLE grid_search_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    params_json     TEXT NOT NULL,
    metrics_json    TEXT NOT NULL,
    created_at      INTEGER NOT NULL
);
```

---

## 4) Time Alignment and Bar Building

### 4.1 Canonical Timebase
- All timestamps normalized to **UTC**
- `ts_min = floor(ts_ms / 60000) * 60000`

### 4.2 Minute OHLCV Construction
- Build from trades within the minute
- Volume = sum of trade sizes
- VWAP = sum(price * size) / sum(size)

### 4.3 L1 Snapshot at Minute Close
- "Minute close time" = ts_min + 59999ms
- Use latest quote at or before that time
- If no quote in last few seconds: **use last available** (no invalidation)

### 4.4 Trade-Quote Alignment
- For each trade, find latest L1 quote at or before trade.ts
- Max allowed staleness: 250ms (configurable)
- If quote older than staleness threshold: **use stale quote** (accept potential error)

---

## 5) Feature Computations

### 5.1 Mid Price
```python
mid = (bid_px + ask_px) / 2
```
Store `mid_close` at minute close.

### 5.2 Rolling Volatility (4h)
Compute each minute using last 240 1m closes:
```python
r_t = log(mid_t / mid_{t-1})
sigma_240 = stdev(r_{t-239...t})
```

**Missing bar handling:** Treat as zero return (no price change).

### 5.3 Rolling Average Spread (60 min)
```python
spread = ask_px - bid_px
spread_avg_60m = mean(spread over last 60 minutes)
```
Used for boundary crossing validation and pullback thresholds.

### 5.4 Volatility-Scaled Bin Width

#### Definition
```python
bin_width_raw = alpha_bin * mid_close * sigma_240
bin_width = round_to_tick(bin_width_raw)
```

#### Constraints
- `bin_width >= tick_size`
- `bin_width <= bin_width_max` (default: 200 * tick_size)

#### Update Schedule
- Compute bin_width every minute
- **Rebucketing:** Apply new bin_width if:
  - `abs(bin_width_new - bin_width_old) / bin_width_old >= 0.25`
  - OR `t % 15 == 0` (every 15 minutes)
- **Rebuild method:** Full rebuild from raw trades (last 4h stored in raw.duckdb)

### 5.5 Rolling 4h Volume Profile (VAP)

#### Base Histogram Resolution
- **Base bin width:** `tick_size` (finest resolution)
- Maintain fixed-bin rolling histogram at base resolution
- Aggregate to volatility-scaled bins on demand

#### Rolling Window Maintenance
```python
# Data structures
deque_minute_hist: Deque[Dict[float, float]]  # length 240
rolling_hist_base: Dict[float, float]         # aggregated

# Every minute:
1. Build minute histogram from trades:
   base_bin_key = floor(trade_price / base_bin) * base_bin
   accumulate size

2. Append to deque, add to rolling_hist_base

3. If deque > 240: pop oldest, subtract from rolling_hist_base
```

#### Aggregation to Vol-Scaled Bins
```python
k = round(bin_width / base_bin)
agg_bin_key = floor(base_bin_key / (k * base_bin)) * (k * base_bin)
rolling_hist_agg[agg_bin_key] += rolling_hist_base[base_bin_key]
```

### 5.6 POC, VAH, VAL Computation

#### Algorithm
```python
1. POC = argmax(volume) over aggregated bins

2. V_total = sum(all bin volumes)

3. Expand outward from POC:
   - Choose next highest-volume adjacent bin
   - Continue until V_cum / V_total >= 0.70

4. Set:
   - VAL = lowest bin included (strictly contiguous)
   - VAH = highest bin included
```

#### Edge Cases
- **Empty histogram:** VA invalid, do not trade
- **POC at edge:** Expand only inward
- **Gaps in distribution:** VA must be **strictly contiguous** (include zero-volume bins)

#### Validity Requirement
- **Minimum 20 bins** with volume required for valid VA
- If fewer bins: suppress signals

### 5.7 Initiative Metric: Inferred Order Flow

#### Trade-Side Inference
```python
for each trade:
    quote = latest L1 at or before trade.ts (max 250ms stale)

    if trade_price >= ask_px:
        sign = +1  # buy-initiated
    elif trade_price <= bid_px:
        sign = -1  # sell-initiated
    else:
        sign = 0   # ambiguous

        # Optional tick-rule fallback (zero-tick continuation):
        if use_tick_rule_fallback:
            if trade_price > prev_trade_price:
                sign = +1
            elif trade_price < prev_trade_price:
                sign = -1
            else:
                sign = prev_sign  # zero-tick continuation
```

#### Aggregation Per Minute
```python
# Aggregate trades at identical timestamp (avoid split-trade double-counting)
trades_grouped = group_by(ts_ms)

OF_1m = sum(sign * size for all trades in minute)
OF_norm_1m = OF_1m / total_volume_1m

ambiguous_trade_frac_1m = ambiguous_volume / total_volume
```

#### High Ambiguous Fraction Handling
- If `ambiguous_trade_frac > 0.35`: **require dual confirmation** (both OF AND qimb must align)

### 5.8 Quote Imbalance

#### Computation
```python
# Raw at minute close
qimb_close = (bid_sz - ask_sz) / (bid_sz + ask_sz)

# EMA over full minute (all quote updates in the minute)
qimb_ema_60s = EMA(qimb_samples, span=60 seconds)
```

Store both `qimb_close` and `qimb_ema_60s` for analysis.

### 5.9 BVC Proxy (Manual Fallback Only)

**Not auto-engaged.** Only used if explicitly configured.

```python
delta_price = close_t - close_{t-1}
std_delta = stdev(delta_price over lookback_bvc)
buy_frac = NormalCDF(delta_price, 0, std_delta)
bvc_net = volume_t * (2 * buy_frac - 1)
```

---

## 6) Signal Engine

### 6.1 State Machine

**States:**
- `FLAT` — No position
- `LONG` — Long position open
- `SHORT` — Short position open
- `PENDING_RETEST` — Waiting for pullback after breakout acceptance

**Rules:**
- One position at a time
- **Flip-on-signal enabled by default** (can reverse without explicit close)
- Flip counts as **two trades** (close + open) for analytics

### 6.2 Acceptance Definitions

| Parameter | Default | Description |
|-----------|---------|-------------|
| `accept_outside_k` | 3 | Consecutive closes outside VA for acceptance |
| `accept_inside_k` | 1 | Closes inside VA to confirm break-in |

**Sequence Tracking Rules:**
- **Close only matters** — Wicks ignored for acceptance counting
- **Hard reset on 1 close** — Any close inside VA resets counter to 0
- **Use original VAH/VAL** — Lock VA boundary at sequence start until complete/reset
- **Independent tracking** — Track acceptance sequences regardless of position state

### 6.3 Boundary Crossing Validation

For gap handling (price jumps through VA boundary):
- **Require crossing:** Must have at least one trade within N ticks of VA boundary
- **N = 1x typical spread** (rolling 60-min average spread)
- Gaps without crossing: Do not count for acceptance

### 6.4 Setup A — Break-in Mean Reversion

**Long break-in from below:**
```
Trigger at minute t close:
1. low_t < VAL_t                    (strict inequality)
2. close_t > VAL_t
3. Boundary crossing validated      (trade within 1x spread of VAL)
4. Initiative confirmation:
   - Normal: OF_1m > of_entry_min OR qimb > qimb_entry_min
   - High ambiguous: OF_1m > of_entry_min AND qimb > qimb_entry_min
5. Cooldown satisfied
6. No kill switch active
```

**Action:** `ENTER_LONG`

**Stop/Targets:**
- `stop_px = low_t - stop_buffer_ticks * tick_size` (trigger bar only)
- `tp1 = POC`
- `tp2 = VAH`

**Short break-in** is symmetric (high > VAH, close < VAH).

### 6.5 Setup B — Breakout Continuation

**Long breakout:**
```
Trigger:
1. close_t > VAH_t
2. k consecutive closes > VAH (using locked VAH at sequence start)
3. Boundary crossing validated for each bar
4. Initiative confirmation (OF/qimb)
```

**Action:** `ENTER_LONG` or `PENDING_RETEST` (if retest mode enabled)

**Retest Mode (included in v0):**
```
After breakout acceptance:
1. Wait for pullback: low within 1x spread of VAH
2. Price closes back above VAH
3. Enter on confirmation

If pullback fails (closes back inside VA):
   → Triggers Setup C (failed breakout)
```

**Pullback threshold:** Configurable, default = 1x typical spread (rolling 60-min)

### 6.6 Setup C — Failed Breakout Reversal

**Short after failed breakout above VAH:**
```
Trigger:
1. high_t > VAH_t
2. close_t < VAH_t
3. Initiative confirmation: OF_1m <= of_fail_min OR qimb <= qimb_fail_min
```

**Action:** `ENTER_SHORT`

**Targets:**
- `tp1 = POC`
- `tp2 = VAL`

### 6.7 Signal Priority (Conflict Resolution)

When multiple signals fire on same bar:

**Fixed hierarchy:** `Break-in > Failed breakout > Breakout`

1. Break-in (mean reversion) — Highest priority
2. Failed breakout (fakeout reversal)
3. Breakout (momentum continuation) — Lowest priority

---

## 7) Position Sizing

### 7.1 Risk-Based Sizing Formula

```python
# Core formula
position_value = (available_margin * risk_pct) / abs(entry_price - stop_price)
position_contracts = position_value / entry_price

# Leverage calculation
required_margin = position_value / leverage
actual_leverage = position_value / available_margin
```

### 7.2 Constraints

| Parameter | Default | Description |
|-----------|---------|-------------|
| `risk_pct` | 0.02 (2%) | Risk per trade as fraction of available margin |
| `max_leverage` | 10 | Maximum leverage (Bybit cross margin) |
| `margin_mode` | cross | Always cross margin |

### 7.3 Leverage Capping

```python
if actual_leverage > max_leverage:
    # Reduce position to fit within 10x leverage
    position_value = available_margin * max_leverage
    position_contracts = position_value / entry_price
    # Alert logged: "Leverage cap hit, position reduced"
```

### 7.4 Capital Basis

- **Use available margin** (not total equity)
- Available margin = equity minus margin reserved for open positions

---

## 8) Partial Exits

### 8.1 Default Split

| Target | Allocation |
|--------|------------|
| TP1 (POC) | 30% |
| TP2 (VAH/VAL) | 70% |

### 8.2 Contract Rounding

- Round 30% to **nearest whole contract**
- Minimum 1 contract for TP1 exit (if position > 1 contract)
- **If position = 1 contract:** Full exit at TP2 only (skip partial)

### 8.3 Post-TP1 Stop Management

After TP1 partial exit:
- **Move stop to breakeven** (entry price)
- Remaining 70% rides to TP2 or stop

---

## 9) Risk Management

### 9.1 Stop Loss

**Stop types by setup:**

| Setup | Stop Location |
|-------|---------------|
| Break-in | Below excursion low (trigger bar only) - stop_buffer |
| Breakout | Inside VA (VAH for long) or below pullback low |
| Failed breakout | Above poke high + stop_buffer |

**Breakout stop logic:** Either condition triggers exit (close inside VA OR price below pullback low).

**Stop buffer:** `stop_buffer_ticks * tick_size` (default: 2 ticks)

### 9.2 Time Stop

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_hold_minutes` | 60 | Base hold time before time exit |

**Extension logic:**
- If position is profitable (net of fees): **trail while profitable**
- Profit threshold: Must cover estimated fees to be considered "profitable"
- No cap on extension while profitable

**Timer start:** Bar close after confirmed fill

### 9.3 Cooldown

| Parameter | Default | Description |
|-----------|---------|-------------|
| `cooldown_minutes` | 3 | No entries after exit |

**Scope:** Global cooldown (no trades in either direction)

### 9.4 Daily Loss Kill Switch

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_daily_loss` | configurable | Max loss before halt |

**Period:** UTC calendar day (reset at 00:00 UTC)

**P&L calculation:** Include funding (true economic P&L)

**No streak limit** — Rely on daily P&L kill switch only

### 9.5 Heat Tracking

Track and display current risk exposure:
```python
heat = sum(position_risk) / available_margin
```

Display **prominently on dashboard** with color-coded warnings.

---

## 10) Execution Engine

### 10.1 Order Types

| Scenario | Order Type |
|----------|------------|
| Entry (default) | Limit order |
| Entry (timeout) | Convert to market after 1 minute |
| Exit (stop/TP/time) | Market order |

### 10.2 Limit Order Handling

- Submit limit order at signal price
- If not filled within **1 minute**: convert to market
- IOC not used (give limit chance to fill)

### 10.3 API Integration

- **Primary:** WebSocket for order updates
- **Fallback:** REST API for reliability/reconciliation
- **Reconnection:** Immediate retry with exponential backoff (max 60 seconds)

### 10.4 Position Reconciliation

If exchange position doesn't match system state:
- **Alert and halt** trading
- Require manual resolution
- Do not auto-reconcile

### 10.5 TP/Stop Same-Bar Handling

If both TP and stop are breached in same bar (backtest):
- **Assume worst case** — Stop hit first (conservative)

---

## 11) Operating Modes

### 11.1 Mode Overview

| Mode | Description |
|------|-------------|
| `dry_run` | Process data, generate signals, no execution |
| `paper` | Simulated execution with bid/ask fill model |
| `shadow` | Live signals generated, paper trades executed, compare |
| `live` | Real execution on Bybit |

### 11.2 Mode Transitions

- **Require flat** position before switching modes
- Paper and live use **same fill model**

### 11.3 Shadow Mode

- Generate signals for both paper and live
- Execute only paper trades
- On divergence: **alert only** (continue running)

### 11.4 Live Trading Activation

- System starts in **observation mode** (signals generated, not acted upon)
- **Manual activation** required by operator to enable trading
- Full 240-minute warmup required before activation allowed

---

## 12) Backtesting

### 12.1 Implementation

- **New Rust implementation** (not TradeCore's backtester)
- Supports bid/ask fill modeling as specified

### 12.2 Fill Model

**Decision time:** End of minute t (bar finalized)

**Fill pricing:**
```python
# Market buy
fill_price = ask_px + slippage_ticks * tick_size

# Market sell
fill_price = bid_px - slippage_ticks * tick_size

# Limit order (if filled)
fill_price = limit_price  # Maker fee applies
```

**Fee model:**
```python
taker_fee = filled_notional * taker_fee_bps / 10000
maker_fee = filled_notional * maker_fee_bps / 10000  # negative = rebate
```

### 12.3 Funding Model

- **Backtest:** Fixed average rate (configurable, e.g., 0.01% per 8h)
- **Live:** Fetch actual rates from Bybit API

### 12.4 Look-Ahead Prevention

- Features for minute t use only data ≤ t close
- VAH/VAL/POC computed from last 240 minutes ending at t
- Entry based on close(t); fill at first quote AFTER close(t)

### 12.5 Parallel Execution

- Configurable worker count
- Default: `CPU cores / 2`
- No timeout per run (run to completion)

### 12.6 Output Metrics

- Gross and net P&L
- P&L per trade
- Win rate, avg win/loss
- Max drawdown
- Sharpe ratio
- Spread paid (avg and distribution)
- Ambiguous trade fraction stats

---

## 13) Grid Search

### 13.1 Sweepable Parameters

**All configurable parameters** can be swept:

| Category | Parameters |
|----------|------------|
| VA | `alpha_bin`, `va_fraction`, `accept_outside_k` |
| Signal | `of_entry_min`, `qimb_entry_min`, `of_breakout_min`, `qimb_breakout_min`, `of_fail_min`, `qimb_fail_min` |
| Risk | `max_hold_minutes`, `cooldown_minutes`, `stop_buffer_ticks`, `risk_pct` |
| Execution | `slippage_ticks_entry`, `slippage_ticks_exit` |

### 13.2 Output Storage

- Results stored in **SQLite** (`execution.db` → `grid_search_results` table)
- Each run: `params_json`, `metrics_json`, timestamp

### 13.3 Visualization

- **Web dashboard:** Browse and compare results
- **CLI:** Export to CSV for external analysis
- Both views available

---

## 14) Web Dashboard

### 14.1 Tech Stack

- Backend: FastAPI
- Frontend: Reuse TradeCore's `total-core-v2.js` footprint charts
- No authentication (local network/VPN assumed)

### 14.2 Views

**Full Trading View (must-have):**
- Current position and P&L chart
- Live value area with price overlay
- Active signals and recent trades
- Order flow metrics (OF_1m, qimb)

**VA Display:**
- **Both views available** (tabbed/toggled):
  - Horizontal histogram (traditional TPO style)
  - Colored price levels (VAH/VAL/POC lines on chart)

**Quality Metrics:**
- `ambiguous_trade_frac_1m` displayed **prominently**
- Color-coded warning thresholds

**Heat Display:**
- Current risk exposure as % of capital
- Prominent placement with warnings

**Grid Search Results:**
- Browse parameter combinations
- Compare metrics across runs

### 14.3 Data Sources

- Footprint/order flow visualization from TradeCore's frontend code
- Real-time updates via WebSocket to dashboard

---

## 15) Monitoring and Alerts

### 15.1 Alert Channel

- **Generic webhook** (user routes to Slack, Discord, Telegram, email, etc.)
- Single endpoint, JSON payload

### 15.2 Alert Types (All Equal Severity)

| Alert | Trigger |
|-------|---------|
| Kill switch activated | Daily loss limit breached |
| Data feed stale | No quotes for > X seconds |
| VA invalid | Fewer than 20 bins in histogram |
| High ambiguous fraction | `ambiguous_trade_frac > 0.35` |
| Position mismatch | Exchange vs system state differ |
| Shadow divergence | Paper and live signals differ |
| Leverage cap hit | Position reduced due to 10x limit |

### 15.3 Logging

- **Feature snapshot every minute** (full state logged)
- Order lifecycle: submit time, fill time, fill px, fee, slippage
- Risk events: kill switch, data staleness, reconciliation issues

---

## 16) System Startup and Recovery

### 16.1 Warmup

- **Full 240 minutes** required before signals valid
- System cannot activate trading until warmup complete

### 16.2 Auto-Reconstruct on Restart

- Load state from **full state snapshots** (every 5 minutes)
- Reconstruct: position state, acceptance counters, pending orders, account state
- Resume automatically once state loaded

### 16.3 Snapshot Persistence

- Save every 5 minutes (periodic)
- Save on any state change (position open/close, order fill)

---

## 17) Configuration

### 17.1 File Structure

```
auction-trader/
├── config/
│   ├── default.yaml      # Default parameters
│   └── .env              # Secrets (gitignored)
```

### 17.2 Environment Variables (Secrets)

```bash
BYBIT_API_KEY=xxx
BYBIT_API_SECRET=xxx
ALERT_WEBHOOK_URL=https://...
DATABASE_DIR=/path/to/data
```

### 17.3 Default Configuration (YAML)

```yaml
# auction-trader default configuration

instrument: BTCUSDT
exchange: bybit
timeframe: 1m
rolling_window_minutes: 240

# Value Area
va_fraction: 0.70
base_bin_ticks: 1
alpha_bin: 0.25
bin_width_max_ticks: 200
rebucket_interval_minutes: 15
rebucket_change_pct: 0.25
min_va_bins: 20

# Initiative
max_quote_staleness_ms: 250
ambiguous_trade_frac_max: 0.35
use_tick_rule_fallback: true

# Quote Imbalance
use_qimb: true
qimb_entry_min: 0.10
qimb_breakout_min: 0.10
qimb_fail_min: -0.10
qimb_ema_span_seconds: 60
spread_lookback_minutes: 60

# Order Flow Thresholds (either/or logic)
of_entry_min: 0
of_entry_min_norm: 0.1
of_breakout_min: 0
of_breakout_min_norm: 0.1
of_fail_min: 0
of_fail_min_norm: -0.1

# Acceptance
accept_outside_k: 3

# Position Sizing
risk_pct: 0.02
max_leverage: 10
margin_mode: cross

# Partial Exits
tp1_pct: 0.30
tp2_pct: 0.70
move_stop_to_breakeven_after_tp1: true

# Risk
max_hold_minutes: 60
extend_if_profitable: true
cooldown_minutes: 3
stop_buffer_ticks: 2

# Execution
fill_on_next_quote: true
slippage_ticks_entry: 1
slippage_ticks_exit: 1
taker_fee_bps: 5
maker_fee_bps: -1
limit_order_timeout_minutes: 1
use_limit_for_entry: true
use_market_for_exit: true

# Backtest
funding_rate_8h_bps: 1  # 0.01% per 8h
backtest_workers: auto  # CPU/2

# Modes
enable_retest_mode: true
enable_flip_on_signal: true

# Monitoring
state_snapshot_interval_minutes: 5
log_features_every_minute: true

# Startup
require_manual_activation: true
warmup_minutes: 240
```

---

## 18) Rust Crate Structure

### 18.1 Workspace Layout

```
auction-trader-core/
├── Cargo.toml              # Workspace manifest
├── crates/
│   ├── core/               # Shared types, config, utils
│   │   └── src/lib.rs
│   ├── ingestion/          # Trade/quote normalization
│   │   └── src/lib.rs
│   ├── features/           # VA, OF, sigma computation
│   │   └── src/lib.rs
│   └── backtest/           # Simulation engine
│       └── src/lib.rs
└── python/
    └── auction_trader/     # PyO3 bindings
        └── src/lib.rs
```

### 18.2 PyO3 Integration

```rust
// Python-exposed functions
#[pyfunction]
fn compute_features(bars: Vec<Bar>, config: Config) -> Features;

#[pyfunction]
fn run_backtest(data: BacktestData, params: Params) -> BacktestResult;
```

---

## 19) Reused Components

### 19.1 From TradeCore

| Component | Location | Usage |
|-----------|----------|-------|
| Footprint chart | `total-core-v2.js` | Dashboard visualization |
| Volume profile calc | `build_candles_with_profiles()` | Reference for VA computation |

### 19.2 From Total-Trader

| Component | Location | Usage |
|-----------|----------|-------|
| WebSocket service | `websocket_service.py` | Bybit data ingestion (initial Python version) |
| Exchange service | `exchange_service.py` | Reference for Bybit v5 API patterns |

---

## 20) Example Calculations

### 20.1 Position Sizing Example

```
Scenario:
- Available margin: $10,000
- Risk per trade: 2%
- Entry price: $50,000
- Stop price: $49,500 (1% below entry)

Calculation:
risk_amount = $10,000 * 0.02 = $200
stop_distance = $50,000 - $49,500 = $500
position_value = $200 / ($500 / $50,000) = $200 / 0.01 = $20,000
position_btc = $20,000 / $50,000 = 0.4 BTC

Leverage check:
actual_leverage = $20,000 / $10,000 = 2x
2x < 10x max → Position approved

Result: Enter long 0.4 BTC at $50,000, stop at $49,500
```

### 20.2 Partial Exit Example

```
Scenario:
- Position: 0.4 BTC long
- TP1 (POC): $50,500
- TP2 (VAH): $51,000

TP1 Exit:
exit_qty = round(0.4 * 0.30) = round(0.12) = 0.12 BTC
remaining = 0.4 - 0.12 = 0.28 BTC

After TP1:
- Move stop to $50,000 (breakeven)
- 0.28 BTC rides to TP2 or stop
```

### 20.3 Value Area Computation Example

```
Rolling 4h histogram (aggregated to vol-scaled bins):

Bin        Volume
$49,800    100
$49,900    250
$50,000    400  ← POC (highest volume)
$50,100    300
$50,200    150
$50,300    50

Total volume = 1,250
Target VA volume = 1,250 * 0.70 = 875

Expansion from POC:
1. Start: $50,000 (400) → cumulative = 400
2. Add $50,100 (300) → cumulative = 700
3. Add $49,900 (250) → cumulative = 950 ≥ 875 ✓

Result:
- POC = $50,000
- VAL = $49,900
- VAH = $50,100
- VA coverage = 950/1250 = 76%
```

### 20.4 Order Flow Inference Example

```
Trades in minute:
ts        price     size    bid_px    ask_px    inferred_side
12:00:01  50,001    0.1     50,000    50,001    +1 (at ask)
12:00:02  50,000    0.2     50,000    50,001    -1 (at bid)
12:00:03  50,000.5  0.15    50,000    50,001    0  (ambiguous)
12:00:04  50,001    0.25    50,000    50,001    +1 (at ask)

With tick-rule fallback for ambiguous:
  50,000.5 > 50,000 (prev) → sign = +1

OF_1m = (0.1 * 1) + (0.2 * -1) + (0.15 * 1) + (0.25 * 1)
      = 0.1 - 0.2 + 0.15 + 0.25 = 0.3

total_volume = 0.1 + 0.2 + 0.15 + 0.25 = 0.7
OF_norm_1m = 0.3 / 0.7 = 0.43

ambiguous_frac = 0.15 / 0.7 = 0.21 (below 0.35 threshold)
```

### 20.5 Break-in Signal Example

```
Minute t state:
- VAL = $49,900
- VAH = $50,100
- POC = $50,000
- Bar: O=$49,850, H=$49,920, L=$49,820, C=$49,950
- OF_1m = +0.5 (positive)
- qimb = +0.15 (positive)
- ambiguous_frac = 0.20 (normal)

Checks:
1. low ($49,820) < VAL ($49,900) ✓
2. close ($49,950) > VAL ($49,900) ✓
3. Crossing validated (trades near $49,900) ✓
4. OF_1m (0.5) > of_entry_min (0) ✓
5. Cooldown satisfied ✓
6. No kill switch ✓

Signal: ENTER_LONG (break-in from below)

Entry: $49,950 (or limit at $49,900)
Stop: $49,820 - 2 ticks = $49,818
TP1: $50,000 (POC)
TP2: $50,100 (VAH)
```

---

## 21) Test Plan

### 21.1 Unit Tests (Comprehensive Coverage)

**Feature Engine (Rust):**
- Quote/trade alignment: correct quote chosen for trade timestamp
- Trade-side inference: bid/ask boundary cases, tick-rule fallback
- Rolling histogram add/subtract consistency
- VAH/VAL/POC correctness (toy histograms)
- Volatility calculation with missing bars
- Bin width scaling and rebucketing

**Signal Engine (Python):**
- Each setup trigger conditions
- Acceptance sequence counting and reset
- Signal priority resolution
- State machine transitions

**Position Sizing:**
- Risk-based calculation
- Leverage capping
- Partial exit rounding

### 21.2 Integration Tests

- Replay short tick sample and confirm:
  - Same features every run (deterministic)
  - No look-ahead (fill times always after signal time)
  - Correct state transitions FLAT→LONG→FLAT
- End-to-end signal generation from raw data

### 21.3 Backtest Validation

- Compare outcomes under:
  - 0 slippage vs 1 tick slippage
  - Taker vs maker fee assumptions
- Sensitivity analysis:
  - `alpha_bin` sweep
  - `accept_outside_k` sweep
  - Risk percentage sweep

---

## 22) Future Enhancements (Post-v0)

- Full P/B/D profile-shape classification
- Multi-strategy parallel execution
- Walk-forward optimization
- ML-based parameter adaptation
- Multi-timeframe bias layer (15m/1h VA + 1m entries)
- Order book depth features (beyond L1)
- Rust WebSocket migration
- Advanced execution algorithms

---

## Appendix A: File Locations

### Existing Code to Reuse

```
# TradeCore footprint charts
/home/soka/Desktop/TradeCore/frontend/total-core-v2.js
/home/soka/Desktop/TradeCore/frontend/total-core-v2.html

# Total-Trader WebSocket service
/home/soka/Desktop/Total-Trader-v0.11/backend/app/services/websocket_service.py
/home/soka/Desktop/Total-Trader-v0.11/backend/app/services/exchange_service.py
```

### New Project Structure

```
/home/soka/Desktop/exp1/auction-trader/
├── config/
│   ├── default.yaml
│   └── .env.example
├── rust/
│   └── auction-trader-core/
│       ├── Cargo.toml
│       └── crates/
├── python/
│   └── auction_trader/
│       ├── __init__.py
│       ├── collector.py
│       ├── signal_engine.py
│       ├── execution.py
│       └── dashboard/
├── frontend/
│   └── (adapted from TradeCore)
├── data/
│   ├── raw.duckdb
│   ├── features.duckdb
│   ├── signals.db
│   └── execution.db
├── tests/
├── scripts/
└── specv2.md
```
