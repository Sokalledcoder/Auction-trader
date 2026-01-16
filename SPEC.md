# spec.md — BTC Perps 1m “PBD / Auction Market Theory” System (Bybit)

**Spec version:** 0.1  
**Instrument:** BTC perpetual (Bybit)  
**Mode:** Directional, single-position, 1 contract  
**Primary TF:** 1-minute  
**Core window (“rolling session”):** 4 hours (240 minutes)  
**Core ideas implemented mechanically:**
- Balance/Value Area (acceptance) vs Imbalance (price discovery)
- Value-area boundaries: VAH / VAL / POC
- Initiative confirmation via order-flow proxy (inferred from prints vs bid/ask) and/or quote imbalance  
Order flow is “signed transaction volume” and is useful for short-horizon prediction .

---

## 0) Summary of v0 deliverables

### v0 delivers
1. Data collection + persistence plan compatible with 48h raw retention.
2. Rolling 4h value area engine (VAH/VAL/POC) with volatility-scaled bins.
3. Initiative metrics:
   - inferred signed order flow from trades + L1 (primary) 
   - quote imbalance qimb from L1 (secondary) 
   - optional BVC proxy (fallback) 
4. Signal engine for 3 setups:
   - Break-in mean reversion (failed auction → back into value)
   - Breakout continuation (acceptance outside value)
   - Failed breakout reversal (fakeout back into value)
5. Backtester + paper trader with bid/ask fill modeling (buy at ask, sell at bid). This is necessary for realistic HF-style tests .

### v0 does NOT deliver
- Full P/B/D *shape classification* (profile-shape recognition)
- Multi-asset / portfolio / hedged spreads
- ML/CPO parameter selection
- Sub-second execution optimization / HFT infrastructure

---

## 1) Glossary

- **VA (Value Area):** price region where the market “accepted” trade (high time/volume).
- **POC:** point of control; price/bin with maximum volume in the profile.
- **VAH / VAL:** value area high/low boundaries containing ~70% of volume (configurable).
- **Acceptance:** persistence of closes outside/inside VA (operational definition below).
- **Initiative:** aggressive crossing (market taking) approximated by signed order flow .
- **OF_1m:** inferred signed order flow over 1 minute.
- **qimb:** quote imbalance = (bid_size − ask_size)/(bid_size + ask_size) .
- **BVC:** Bulk Volume Classification proxy for order flow from bars/volume .

---

## 2) System architecture

### 2.1 Components
1. **Collector**
   - Ingest trades (prints), L1 bid/ask, and optionally 1m candles.
2. **Normalizer**
   - Time alignment (trade ↔ latest quote).
   - Minute bucketing.
3. **Feature Engine**
   - Rolling 4h value profile (VAP) → VAH/VAL/POC.
   - Volatility estimate and dynamic bin width.
   - Initiative metrics: OF_1m, qimb, (optional BVC).
4. **Signal Engine**
   - Evaluate setups A/B/C.
   - Output “intents” (enter/exit/hold).
5. **Execution Engine**
   - Paper/live switch.
   - Order placement, state machine, risk checks.
6. **Backtester**
   - Tick/quote-aware simulation, enforcing bid/ask fills .
7. **Monitoring**
   - Logs, metrics, alerts.

### 2.2 Runtime cadence
- Feature updates: **every minute**
- Signal evaluation: **at minute close** (bar finalization)
- Order submission: immediately after signal evaluation
- Fill model: first quote after decision timestamp (paper/backtest), or actual fills (live)

---

## 3) Data requirements and storage

### 3.1 Live inputs (minimum)
**Trades (prints)**
- ts_ms (int64)
- price (float)
- size (float)

**L1 quotes**
- ts_ms (int64)
- bid_px, bid_sz
- ask_px, ask_sz

**Optional**
- exchange-provided 1m candles

> Minute-binned data is widely used for intraday research; minute bars underpin many microstructure models and volume/spread profiles are rarely built finer than 1 minute .

### 3.2 Retention policy
- Raw trades + quotes: keep rolling **48 hours**, then purge
- Derived per-minute features/signals: keep indefinitely (small footprint)

### 3.3 Persistent schema (recommended)

#### Table: `bars_1m`
- ts_min (int64, unix minute boundary, UTC)
- open, high, low, close (float)
- volume (float)
- vwap (float, optional)
- bid_px_close, ask_px_close, bid_sz_close, ask_sz_close (L1 snapshot at minute close)

#### Table: `features_1m`
- ts_min
- mid_close
- sigma_240 (rolling 4h volatility)
- bin_width (vol-scaled bin width)
- poc, vah, val
- va_coverage (e.g., 0.70)
- of_1m (signed order flow inferred)
- of_norm_1m (of_1m / volume)
- qimb_close
- ambiguous_trade_frac_1m (quality metric)
- (optional) bvc_buy_frac_1m, bvc_net_proxy_1m

#### Table: `signals_1m`
- ts_min
- signal_breakin_long, signal_breakin_short (bool)
- signal_breakout_long, signal_breakout_short (bool)
- signal_failbreak_long, signal_failbreak_short (bool)
- acceptance_state (enum/int)
- chosen_action (enum: ENTER_LONG, ENTER_SHORT, EXIT, HOLD)
- reason_code (string)

#### Table: `trades_exec`
- trade_id
- ts_ms_submit, ts_ms_fill
- side (BUY/SELL)
- qty (contracts)
- order_type (MKT/LMT)
- fill_px
- fee
- funding_cost (allocated)
- pnl_realized
- pnl_unrealized_at_close (optional)
- strategy_tag (breakin/breakout/failbreak)

---

## 4) Time alignment and bar building

### 4.1 Canonical timebase
- All timestamps normalized to **UTC**.
- Define `ts_min = floor(ts_ms / 60000) * 60000`.

### 4.2 Minute OHLCV construction
- Use trades to build OHLCV (preferred), or exchange candles.
- Volume = sum of trade sizes in the minute.

### 4.3 L1 snapshot at minute close
- Define “minute close time” as ts_min + 59999ms.
- Use latest quote at or before that time as the close snapshot.

---

## 5) Feature computations

## 5.1 Mid price
- `mid = (bid_px + ask_px) / 2`

Store `mid_close` at minute close.

---

## 5.2 Rolling volatility (4h)
Compute each minute using the last 240 1m closes (or mids):
- `r_t = log(mid_t / mid_{t-1})`
- `sigma_240 = stdev(r_{t-239...t})`

Optionally use EWMA stdev instead for smoother behavior.

---

## 5.3 Volatility-scaled bin width
Goal: bins expand when volatility is high and shrink when volatility is low.

### 5.3.1 Definition
- `bin_width_raw = alpha_bin * mid_close * sigma_240`
- `bin_width = round_to_tick(bin_width_raw)`

### 5.3.2 Constraints
- `bin_width >= tick_size`
- `bin_width <= bin_width_max` (e.g., 200 * tick_size)

### 5.3.3 Update schedule
- Compute bin_width every minute.
- **Rebucketing**: only apply a new bin_width if:
  - `abs(bin_width_new - bin_width_old)/bin_width_old >= rebucket_change_pct`
  - OR `t % rebucket_interval_minutes == 0`

Default: rebucket_interval_minutes=15, rebucket_change_pct=0.25.

---

## 5.4 Rolling 4h volume profile (VAP) and value area

### 5.4.1 Design choice (important): base histogram resolution
To support volatility-scaled bins without constantly rebuilding from raw trades, maintain a **base histogram at minimal resolution** and aggregate to wider bins on demand.

**Base bin width:** `base_bin = tick_size` (or small multiple of tick_size, e.g., 2 ticks).

#### Rationale
- You can update a fixed-bin rolling histogram incrementally per minute.
- Then compute vol-scaled bins by grouping base bins into larger bins; no need to rebuild the past 4 hours’ trades when bin_width changes.

### 5.4.2 Rolling window maintenance
Maintain:
- `deque_minute_hist` length 240, each entry is a dict: `{base_price_bin: volume}`
- `rolling_hist_base` dict of `{base_price_bin: volume}` for last 240 minutes

Every minute:
1. Build minute histogram from trades in that minute:
   - `base_bin_key = floor(trade_price / base_bin) * base_bin`
   - accumulate size
2. Add minute hist to `deque_minute_hist`, add volumes into `rolling_hist_base`
3. If deque exceeds 240, pop oldest hist and subtract its volumes from `rolling_hist_base` (delete keys when volume ~0)

### 5.4.3 Aggregation to vol-scaled bins
At signal computation time, compute aggregated histogram:
- `k = round(bin_width / base_bin)` (integer)
- `agg_bin_key = floor(base_bin_key / (k*base_bin)) * (k*base_bin)`
- `rolling_hist_agg[agg_bin_key] += rolling_hist_base[base_bin_key]`

### 5.4.4 POC, VAH, VAL
Inputs: `rolling_hist_agg` for last 4h.

1) `POC = argmax volume over agg bins`
2) Compute total volume: `V_total`
3) Expand outward from POC (choose next higher-volume adjacent bin each step) until `V_cum / V_total >= va_fraction` (default 0.70)
4) Set:
   - `VAL = lowest bin included`
   - `VAH = highest bin included`

Edge cases:
- If histogram empty: do not trade; set VA invalid.
- If POC at edge: expand only inward where bins exist.

---

## 5.5 Initiative metric: inferred order flow (primary)

Chan describes order flow as signed transaction volume and notes you can infer sign if you record bid/ask and transaction prices .

### 5.5.1 Trade-side inference
For each trade:
- Align to the latest L1 quote at or before trade.ts (max allowed staleness configurable, default 250ms).
- If `trade_price >= ask_px`: sign = +1
- Else if `trade_price <= bid_px`: sign = −1
- Else: sign = 0 (ambiguous; configurable tick-rule fallback)

### 5.5.2 Aggregation per minute
- `OF_1m = Σ (sign * trade_size)` over minute
- `ambiguous_trade_frac_1m = ambiguous_volume / total_volume`

Quality requirement:
- Track % ambiguous. If ambiguous > threshold (e.g., 0.35), degrade confidence and/or require stronger confirmation (qimb).

---

## 5.6 Quote imbalance (secondary)

Compute quote imbalance at minute close:
- `qimb = (bid_sz - ask_sz) / (bid_sz + ask_sz)` 

Optionally compute:
- `qimb_ema = EMA(qimb, qimb_ema_span)` over last N seconds/minutes.

---

## 5.7 BVC proxy (optional fallback)
Bulk Volume Classification (BVC) estimates buy fraction from price changes and stddev .

Implementation (time bars version; v0):
- `deltaPrice = close_t - close_{t-1}`
- `std_delta = stdev(deltaPrice over lookback_bvc)`
- `buyFrac = NormalCDF(deltaPrice, 0, std_delta)`
- `bvc_net = volume_t * (2*buyFrac - 1)`

Note: BVC prefers volume bars in theory . Keep this as a secondary confirmation in v0.

---

## 6) Signal engine

### 6.1 State machine
States:
- FLAT
- LONG
- SHORT

Rules:
- Only 1 position at a time (1 contract).
- No pyramiding in v0.
- If in a position, only process exits/stops; ignore new entries unless “flip-on-signal” enabled.

### 6.2 Acceptance definitions (configurable)
We need mechanical “acceptance” rules:
- `accept_outside_k`: require k consecutive closes outside VA boundary (default 3)
- `accept_inside_k`: require k consecutive closes inside VA after excursion (default 1, because break-in already uses close back inside)

---

## 6.3 Setup A — Break-in mean reversion (failed auction → back into value)

**Long break-in-from-below**
Trigger at minute t close:
1) `low_t < VAL_t`
2) `close_t > VAL_t`
3) Initiative confirmation:
   - `OF_1m_t > of_entry_min` (default 0)
   - AND/OR `qimb_t > qimb_entry_min` (default 0.1)
4) Optional: cooldown satisfied and no-trade filters pass

Action:
- ENTER_LONG

Stop/targets:
- stop_px = min(low_excursion) - stop_buffer
- tp1 = POC
- tp2 = VAH

**Short** is symmetric:
- `high_t > VAH_t` AND `close_t < VAH_t` etc.

---

## 6.4 Setup B — Breakout continuation (acceptance outside value)

**Long breakout**
Trigger:
1) `close_t > VAH_t`
2) `close_{t-k+1...t} > VAH_t` (k consecutive closes)
3) Confirmation:
   - `OF_1m_t >= of_breakout_min` and/or `qimb_t >= qimb_breakout_min`

Action:
- ENTER_LONG (or set “pending retest” mode if you require retest entry)

**Retest entry (optional v0 toggle)**
- After breakout trigger, wait for price to pull back:
  - `low_{t..t+N} <= VAH + retest_buffer`
  - and closes back above VAH
- Enter on that confirmation.

---

## 6.5 Setup C — Failed breakout reversal (fakeout)

**Short after failed breakout above VAH**
Trigger:
1) `high_t > VAH_t`
2) `close_t < VAH_t`
3) Confirmation:
   - `OF_1m_t <= of_fail_min` (negative)
   - and/or `qimb_t <= qimb_fail_min` (negative)

Action:
- ENTER_SHORT

Targets:
- tp1 = POC
- tp2 = VAL

---

## 7) Risk management (v0)

### 7.1 Stops
- Always set a stop immediately upon entry.
- Stop type in live: stop-market or conditional order.

Stop rules:
- Break-in: stop beyond excursion extreme (plus buffer)
- Breakout: stop just inside VA (or below pullback low in retest mode)
- Fail breakout: stop above poke high (plus buffer)

### 7.2 Time stop
- Exit if position held longer than `max_hold_minutes` (default 60)

### 7.3 Cooldown
- After exit, don’t re-enter for `cooldown_minutes` (default 3–5) to reduce churn.

### 7.4 Kill switch
- If daily realized PnL < −max_daily_loss, disable new entries until reset time.

---

## 8) Execution engine

### 8.1 Live order types (v0)
- Market orders for entry/exit (simplest)
- Limit orders on retest (optional toggle)

### 8.2 Paper/backtest fill model
Chan notes you must backtest with bid/ask because you execute by buying at ask and selling at bid .

**Decision time:** end of minute t (after bar finalized).  
**Fill time:** first available quote after decision time.

Fill pricing:
- Market buy: fill at ask + slippage
- Market sell: fill at bid − slippage

Slippage model:
- `slippage_ticks_entry`, `slippage_ticks_exit` (default 1 tick)
- or bps-based: `slippage_bps`

Fees:
- taker fee applied on filled notional
- funding applied per funding schedule (approximate in backtest if needed)

---

## 9) Backtesting

### 9.1 Two backtest modes
**Mode 1: Tick/quote-driven (recommended for the 10-day dataset)**
- Replay trades + quotes in time order.
- Build per-minute bars and features without look-ahead.
- Execute fills at first quote after decision time, as above.
- Stops can trigger intraminute using tick stream.

**Mode 2: Minute-bar-driven (for longer history once you store derived features)**
- Use `features_1m` + `bars_1m` snapshots.
- Approximate fills with bid/ask at minute close or next minute open snapshot.
- Stops approximated using bar high/low (less accurate).

### 9.2 Look-ahead prevention
- Features for minute t must only use data ≤ t close.
- VAH/VAL/POC computed from last 240 minutes ending at t.
- Entry based on close(t); fill at first quote AFTER close(t).

### 9.3 Output metrics
- gross and net PnL
- PnL per trade
- win rate, avg win/loss
- max drawdown
- spread paid (avg and distribution)
- ambiguous trade fraction stats

---

## 10) Configuration (defaults)

```yaml
instrument: BTCUSDT_PERP
timeframe: 1m
rolling_window_minutes: 240

# Value area
va_fraction: 0.70
base_bin_ticks: 1
alpha_bin: 0.25
bin_width_max_ticks: 200
rebucket_interval_minutes: 15
rebucket_change_pct: 0.25

# Initiative
max_quote_staleness_ms: 250
ambiguous_trade_frac_max: 0.35
use_tick_rule_fallback: false

# Quote imbalance
use_qimb: true
qimb_entry_min: 0.10
qimb_breakout_min: 0.10
qimb_fail_min: -0.10

# Order flow thresholds
of_entry_min: 0
of_breakout_min: 0
of_fail_min: 0

# Acceptance
accept_outside_k: 3

# Risk
max_hold_minutes: 60
cooldown_minutes: 3
stop_buffer_ticks: 2

# Execution (paper/backtest)
fill_on_next_quote: true
slippage_ticks_entry: 1
slippage_ticks_exit: 1
taker_fee_bps: 5
maker_fee_bps: -1
funding_model: simple   # v0: flat estimate or replay schedule
11) Monitoring & observability
Required logs
Feature snapshot at each signal:
VAH/VAL/POC/bin_width, OF_1m, qimb, ambiguous_trade_frac
Order lifecycle:
submit time, fill time, fill px, fee, slippage estimate
Risk events:
kill switch triggers, data staleness halts
Alerts
Data feed stale > X seconds
ambiguous_trade_frac_1m spikes above threshold
VA computation fails (empty histogram)
daily loss limit breached
12) Test plan
12.1 Unit tests
Quote/trade alignment: correct quote chosen for trade timestamp
Trade-side inference: bid/ask boundary cases
Rolling histogram add/subtract consistency
VAH/VAL/POC correctness (toy histograms)
12.2 Integration tests
Replay a short tick sample and confirm:
same features every run (deterministic)
no look-ahead (fill times always after signal time)
correct state transitions FLAT→LONG→FLAT
12.3 Backtest validation (10-day dataset)
Compare outcomes under:
0 slippage vs 1 tick slippage
taker vs maker fee assumptions
Sensitivity to bin_width alpha and acceptance_k
13) Future enhancements (post-v0)
Full P/B/D profile-shape classification (skewed “P”, “B”, “D” distributions)
Retest-only breakout entries (reduce spread cost)
Parameter adaptation (CPO-style) on thresholds/acceptance windows
Multi-timeframe bias layer (e.g., 15m/1h value area + 1m entries)
Order book depth features (beyond L1)
Algorithmic Trading_ Winning Strategies and Their Rationale -- Ernie Chan -- John Wiley & Sons, Inc_ (trade), Hoboken, New Jersey, 2013 -- John Wiley -- 9781118460146 -- 2423aa29f449dfb1692e2293f4c6553a -- Anna’s Archive.pdf
Algorithmic Trading and Quantita
