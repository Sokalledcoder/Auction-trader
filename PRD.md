PRD — BTC Perps 1‑Minute “PBD / Auction Market Theory” System (Bybit)
Version: 0.1
Owner: You
Market: Bybit BTC perpetuals (directional, 1 contract)
Primary timeframe: 1‑minute
Core premise: Markets alternate between balance (value acceptance) and imbalance (price discovery). We trade repeatable break‑in / breakout / failed‑auction patterns around a rolling value area, using objective liquidity/initiative measures.

1) Problem statement
You want to implement a systematic interpretation of Auction Market Theory (AMT) / PBD (P/B/D) setups on BTC perps. The main implementation challenge is turning discretionary “auction language” (value areas, acceptance, initiative, liquidity) into measurable features and mechanical rules, especially because Bybit does not provide exchange “aggressor side” (trade side) directly.

2) Goals & success metrics
Goals
Compute a rolling 4‑hour value area (VAH/VAL/POC) from market data, updated every minute.
Detect PBD-relevant events on 1m:
break‑in (failed auction → mean reversion back into value)
breakout acceptance (continuation)
failed breakout (reversal back into value)
Quantify “initiative / aggression” without official trade-side labels using:
trade prints aligned with bid/ask (primary), and/or
BVC-style proxy (fallback), and
quote imbalance (optional filter)
Provide:
a live trading engine (paper first, then live)
a backtest engine that models bid/ask fills and fees
Success metrics (initial)
Backtest (10 trading days raw): net P&L after fees + funding + modeled slippage
Fill realism: average modeled spread paid (bps), slippage distribution, % of trades where fill assumptions are plausible
Stability: signals reproducible (same inputs → same signals), no look-ahead
Live paper trading: similar distribution of outcomes vs backtest over ≥ 7 days
3) Non-goals (v0 scope exclusions)
Multi-asset portfolio (ETH/SOL) or market-neutral spreads
Full AMT “profile shapes” (P vs B vs D) classification by histogram shape (we’ll use value-area mechanics first)
Machine learning / CPO parameter selection (possible future enhancement)
Sub-second HFT execution / colocated infra
4) Users / personas
Primary user: Quant trader/system developer (you)
Secondary user: Monitoring operator (also you) who needs alerts, logs, and dashboards
5) Key assumptions & constraints
Exchange: Bybit
Instrument: BTC perpetual (e.g., BTCUSDT perp)
No official trade-side label from exchange
You can record live tick data indefinitely, but you prefer purging raw ticks every 48 hours
For backtesting you can access ~10 days of historical tick/trade + L1 quote data
Rolling session definition: last 4 hours (240 minutes), updated every minute
Bin sizing: volatility-scaled bins (not fixed $ bins)
6) Data requirements
6.1 Market data feeds (live)
A) Trades (prints)

timestamp (ms preferred)
trade_price
trade_size
B) L1 quotes (you confirmed you have this)

timestamp
best_bid_price, best_bid_size
best_ask_price, best_ask_size
C) 1-minute bars (derived or from exchange)

timestamp (minute close)
OHLC
volume
Minute-binned data is a standard compromise: it’s commonly used for microstructure models and intraday strategies; it provides dimension reduction and avoids excessive noise (volume/spread profiles are rarely built finer than one minute)

6.2 Storage & retention
Raw storage (ephemeral):

trades + L1 quotes retained up to 48h (rotating purge)
Derived storage (persistent):

1m bars
rolling value-area outputs (VAH/VAL/POC, bin width)
initiative metrics per minute (order flow proxy, quote imbalance)
signals & positions
execution logs (orders, fills, simulated fills)
7) Core concepts translated into measurable signals
7.1 “Initiative” / aggressive crossing (order flow)
Chan defines order flow as signed transaction volume and explains why it predicts short-term movement: market buys lift offers; market sells hit bids .

Because you don’t get trade-side labels, we will infer them:

Functional requirement: Trade-side inference using bid/ask

Align each trade to the most recent L1 quote at or before the trade timestamp.
Classify:
buy-initiated if trade_price ≥ ask
sell-initiated if trade_price ≤ bid
else ambiguous (configurable handling: ignore / tick rule fallback)
This is directly aligned with the idea that, if you record bid/ask and trades, you can infer whether trades occurred at bid or ask and compute order flow .

Aggregate per minute:

OF_1m = Σ signed_trade_size
OF_norm_1m = OF_1m / total_volume_1m (range [-1, +1] roughly)
7.2 Fallback initiative proxy: BVC (Bulk Volume Classification)
If trade/quote alignment or classification coverage is too low, implement BVC as a backup. BVC estimates buy volume fraction from bar/volume data using a Gaussian CDF of price change relative to its std dev .

Buy fraction ≈ Z = NormalCDF(deltaPrice, 0, std(deltaPrice over lookback))
Net proxy ≈ V * (2Z − 1)
The book provides an example threshold rule for buy fraction (entryThreshold=0.95, exitThreshold=0.5)  (we will use it only as a filter initially, not necessarily as the whole strategy).

7.3 Liquidity pressure filter: quote imbalance
Compute quote imbalance (a standard microstructure signal):

qimb = (bid_size − ask_size) / (bid_size + ask_size)
Use it as:

a directional filter (only take longs if qimb > +q_th, shorts if qimb < −q_th)
a breakout confirmation (acceptance outside value should have supportive qimb)
8) Rolling 4-hour Value Area engine (VAH/VAL/POC)
8.1 Inputs
Trade prints (preferred) + timestamps
Rolling volatility estimate (from 1m bars)
Bybit tick size
8.2 Volatility estimate
Compute every minute (or every N minutes):
sigma_240 = stdev(1m log returns over last 240 minutes)
robust alternative: ATR-like measure
8.3 Volatility-scaled bin width
Requirement: bin width scales with volatility but remains stable enough for a rolling histogram.

Proposed approach:

bin_width_raw = α * mid_price * sigma_240
bin_width = round_to_tick(bin_width_raw)
Clamp: bin_width ∈ [min_tick, max_bin_width]
Rebucketing rule (important):

Recompute bin_width every rebucket_interval (e.g., 15 min).
If bin width changes by more than X% (e.g., 25%), rebuild the last 4h histogram from stored trades (you have the last 48h).
8.4 Build the Volume-at-Price (VAP) histogram
For the rolling 4h window:

Map each trade_price to a bin: bin = floor(price / bin_width) * bin_width
Accumulate volume: VAP[bin] += trade_size
8.5 Compute POC / VAH / VAL
POC = bin with max volume
Value Area = smallest contiguous set of bins around POC that contains ~70% of total volume (expand outward by highest adjacent volume)
Outputs per minute:

VAH, VAL, POC, total_volume_4h, bin_width
9) Signal detection (PBD setups as mechanical rules)
MVP focuses on “value mechanics” first. Explicit P/B/D shape labeling can be added later.

9.1 Definitions
Value area = [VAL, VAH]
“Inside value” = close ∈ [VAL, VAH]
“Outside above” = close > VAH
“Outside below” = close < VAL
9.2 Setup A — Break‑in mean reversion (failed auction → back through value)
Long break‑in from below

Trigger:
low(t) < VAL
close(t) > VAL (re-entry)
Confirmation:
OF_1m(t) > 0 OR sum(OF_1m over last N minutes) > 0
optionally qimb(t) > q_th
Entry:
configurable: market at next bar open; or limit at/near VAL
Stop:
below excursion low minus buffer (buffer = 1–2 bin_width)
Targets:
TP1 at POC; TP2 at VAH
Short break‑in from above is symmetrical.

9.3 Setup B — Breakout continuation (acceptance outside value)
Long breakout

Trigger:
close(t) > VAH
acceptance: k consecutive closes > VAH
Confirmation:
OF positive and/or qimb positive
Entry:
breakout market entry OR pullback-to-VAH retest entry
Stop:
close back inside value, or below pullback low
Exits:
trailing stop logic (v0 can be simple: opposite acceptance or time stop)
9.4 Setup C — Failed breakout reversal (fakeout)
Short after failed breakout above VAH

Trigger:
high(t) > VAH
close(t) < VAH (back inside)
Confirmation: OF negative and/or qimb negative
Entry: next bar market
Stop: above poke high + buffer
Target: POC then VAL
10) Execution & risk management requirements
10.1 Position sizing
Fixed: 1 contract (directional)
10.2 Order types (configurable)
Market orders (baseline)
Limit orders at value boundary for break-in/retests (optional phase 2)
10.3 Backtest fill model (must be realistic)
For short-horizon strategies you must model the spread. Chan explicitly notes that for these strategies you need to backtest with bid-ask quotes since execution requires buying at the ask and selling at the bid .

Backtest fill rules (v0):

Market buy fills at ask(t_exec)
Market sell fills at bid(t_exec)
Include:
taker fee (bps)
funding (perp)
configurable slippage (ticks or bps)
10.4 Safety controls
Max position time (e.g., 60 minutes) to avoid “stuck” inventory
Daily max loss / kill-switch
Data quality halt (if quotes stale or VAP cannot be computed)
11) Backtesting requirements (10 days raw → scalable derived history)
11.1 What to validate with the 10-day tick dataset
Trade-side inference coverage:
% trades classified at bid or ask vs ambiguous
Relationship between initiative proxy and outcomes:
Does OF_1m sign align with short-horizon continuation more than random?
Spread/fee realism:
how sensitive results are to +1 tick slippage and fee changes
11.2 Derived dataset to keep forever
Store per minute:

VAH/VAL/POC/bin_width
OF_1m (or BVC proxy) and qimb
signal flags + state machine state
simulated fill prices
This lets you run long backtests without keeping raw ticks.

12) System architecture (high-level)
Components
Collector
WS/REST ingestion for trades + L1 + 1m bars
time sync + sequence handling
Normalizer
aligns trades with latest quote snapshot
outputs classified trades
Feature engine
rolling volatility (4h)
VAP histogram (4h)
VAH/VAL/POC
OF_1m / qimb / BVC
Signal engine
evaluates setups A/B/C
produces entry/exit intents
Execution engine
sends orders (paper/live)
logs fills and state
Backtester
replays events + applies bid/ask fills
Monitoring
dashboards, alerts, audit logs
13) Functional requirements (testable)
FR1 — Rolling value area computation
Given trades in last 240 minutes, system outputs VAH/VAL/POC within 1 second of minute close.
Acceptance: VA contains 70% ± 1% of volume (configurable).
FR2 — Volatility-scaled binning
Bin width updates every 15 minutes (configurable).
If bin width changes > 25%, rebuild histogram from last 4h trades.
FR3 — Initiative metrics
Produce OF_1m from classified trades (primary) using bid/ask inference
Produce qimb from L1 snapshots
Optional: BVC proxy available
FR4 — Signal generation
For each minute, output boolean flags for:
break-in long/short
breakout acceptance long/short
failed breakout reversal long/short
Each signal logs: VAH/VAL/POC at decision time + confirmation metrics used.
FR5 — Execution + accounting
Paper/live mode toggle
Fees + funding applied to P&L
Backtest uses bid/ask fills
FR6 — Retention and disk management
Raw ticks and quotes purged after 48h.
Derived features retained indefinitely.
14) Risks & mitigations
Trade-side inference errors (prints not exactly at bid/ask; latency)

Mitigation: allow “ambiguous” bucket; measure coverage; use qimb + BVC as secondary confirmations
Backtest optimism due to fills

Mitigation: enforce bid/ask fills and slippage in backtest
Small historical sample for raw prints (10 days)

Mitigation: treat 10 days as calibration; then run longer tests on derived features; paper trade
Volatility-scaled bins complexity

Mitigation: controlled rebucketing; rebuild last-4h histogram when bins change materially
15) Milestones (suggested)
M1 (1–3 days): Data collector + storage + 1m bar pipeline
M2 (2–4 days): VAP + VAH/VAL/POC + vol-scaled bins
M3 (2–4 days): Trade/quote alignment + order flow proxy + qimb
M4 (2–4 days): Signal engine A/B/C + logging
M5 (3–7 days): Backtester with bid/ask fills + fee/funding/slippage
M6 (5–10 days): Paper trading + monitoring + parameter calibration

16) Open questions (need your decisions)
Entry order type default: market vs limit-at-boundary for break-ins?
Acceptance rule for breakouts: consecutive closes (k) vs time-outside?
Re-entry confirmation: require OF_1m sign, qimb threshold, or both?
If you answer those three, I’ll convert this PRD into an “implementation spec” with exact formulas, default parameter values, and a data schema (tables/columns) you can hand to code directly.

Algorithmic Trading and Quantitative Strategies -- Velu, Raja; Hardy, Maxence; Nehren, Daniel -- 2020.pdf
Algorithmic Trading_ Winning Strateg
