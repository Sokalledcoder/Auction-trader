# PROJECT_INTENT.md — auction-trader

This document captures the background, goals, and design philosophy of the auction-trader project, along with a comprehensive record of all technical decisions made during the specification interview.

---

## Part 1: Project Background and Philosophy

### The Problem

Auction Market Theory (AMT) and Profile-Based Discretionary (PBD) trading approaches have proven effective for understanding market structure, but they traditionally require significant discretionary judgment. Concepts like "value areas," "acceptance," and "initiative" are powerful but subjective.

The challenge: **How do you translate discretionary auction language into measurable features and mechanical rules?**

Additionally, Bybit (our target exchange) does not provide official trade-side labels (aggressor flag), making order flow inference more complex.

### The Vision

Build a systematic trading system that:

1. **Mechanically implements AMT concepts** — Value areas, acceptance, initiative become computable features
2. **Trades high-probability setups** — Break-in (mean reversion), breakout (continuation), failed breakout (reversal)
3. **Uses objective initiative measures** — Inferred order flow from bid/ask alignment, quote imbalance
4. **Executes with proper risk management** — Position sizing based on risk, leverage limits, partial profit taking
5. **Provides realistic backtesting** — Bid/ask fill modeling, fee/funding/slippage accounting

### Core Philosophy

**Simplicity over cleverness.** Version 0 focuses on getting the core mechanics right rather than optimizing for edge cases or adding sophisticated features.

**Mechanical rules over discretion.** Every concept from AMT is translated into a specific, testable condition.

**Realistic testing over optimistic assumptions.** Backtests use bid/ask fills, account for fees and funding, and model slippage.

**Conservative risk management.** Position sizing based on risk, leverage capped at 10x, partial exits to lock in profits.

### Target User

The primary user is a quant trader/developer who:
- Understands market microstructure and AMT concepts
- Can operate and monitor a systematic trading system
- Wants objective, repeatable signals rather than discretionary judgment

---

## Part 2: Goals and Success Metrics

### Primary Goals

1. Compute a rolling 4-hour value area (VAH/VAL/POC) from market data, updated every minute
2. Detect PBD-relevant events: break-in, breakout acceptance, failed breakout
3. Quantify initiative without official trade-side labels using bid/ask inference
4. Provide live trading (paper first, then real) with proper risk controls
5. Provide backtesting with realistic fill modeling

### Success Metrics

- **Backtest realism:** Net P&L after fees + funding + slippage is plausible
- **Fill accuracy:** Average modeled spread, slippage distribution are realistic
- **Determinism:** Same inputs produce same outputs (no randomness, no look-ahead)
- **Paper validation:** Paper trading results similar to backtest over 7+ days

### Explicit Non-Goals (v0)

- Full P/B/D shape classification (histogram shape recognition)
- Multi-asset portfolio or hedged spreads
- ML/CPO parameter selection
- Sub-second HFT execution
- Multi-strategy parallel execution

---

## Part 3: Technical Approach Summary

### Tech Stack Choice: Python + Rust Hybrid

**Rust for hot paths:**
- Data ingestion and normalization
- Feature computation (rolling histogram, VA, order flow)
- Backtesting engine (bid/ask fills, tick replay)

**Python for orchestration:**
- Signal engine and state machine
- Execution and order management
- Web dashboard and monitoring

**Integration:** PyO3 bindings expose Rust functions to Python

### Storage Architecture: 4 Databases

| Database | Engine | Purpose | Retention |
|----------|--------|---------|-----------|
| raw.duckdb | DuckDB | Tick data | 48h rolling |
| features.duckdb | DuckDB | 1m bars, features | Permanent |
| signals.db | SQLite | Signal state | Permanent |
| execution.db | SQLite | Positions, P&L | Permanent |

**Rationale:** DuckDB excels at analytical queries on time-series data. SQLite is simpler for state management and doesn't require external processes.

### Reused Components

**From TradeCore:**
- Footprint chart visualization (`total-core-v2.js`)
- Reference implementation for volume profiles

**From Total-Trader:**
- Bybit WebSocket service (Python, will migrate to Rust later)
- Exchange service patterns for Bybit v5 API

### New Implementations

- **Backtester:** New Rust implementation with proper bid/ask fill model (TradeCore's uses close price only)
- **Feature engine:** Rolling histogram with volatility-scaled bins
- **Signal engine:** State machine for setup detection

---

## Part 4: Design Decisions Q&A

This appendix documents every technical decision made during the specification interview. Each entry shows the question, available options, and the chosen answer.

### Signal Logic

**Q: When trade-side inference produces a high ambiguous fraction (>35%), what behavior should this trigger?**
- Options: Reject all signals | Require dual confirmation | Widen thresholds | Log but proceed
- **Answer: Require dual confirmation** — Must have both OF AND qimb alignment instead of OR

**Q: For rolling histogram rebucketing (bin_width changes >25%), what method should be used?**
- Options: Rebuild from raw trades | Merge/split existing bins | Hybrid approach
- **Answer: Rebuild from raw trades** — Full 4h rebuild from stored tick data (most accurate)

**Q: When multiple setups trigger simultaneously, what's the conflict resolution priority?**
- Options: Fixed hierarchy | Strongest confirmation | No-trade on conflict | First detected wins
- **Answer: Fixed hierarchy** — Break-in > Failed breakout > Breakout

**Q: What should the priority order be?**
- Options: Break-in > Failed > Breakout | Failed > Break-in > Breakout | Breakout > Failed > Break-in | Context-dependent
- **Answer: Break-in > Failed > Breakout** — Mean reversion signals prioritized

### Value Area Computation

**Q: If there are gaps in the volume distribution, should VA be strictly contiguous or can it skip gaps?**
- Options: Strictly contiguous | Allow small gaps | Volume-weighted only
- **Answer: Strictly contiguous** — VA must be continuous range even if some bins have zero volume

**Q: When price gaps through a VA boundary, should this trigger breakout acceptance counting?**
- Options: Gaps count normally | Require crossing | Count but flag
- **Answer: Require crossing** — Must have actual trades near the boundary to count

**Q: How do you define a valid crossing?**
- Options: Any trade near boundary | Trades both sides | Consecutive ticks
- **Answer: Any trade near boundary** — At least one trade within N ticks of VAH/VAL

**Q: What should N be for the boundary crossing check?**
- Options: Fixed ticks | % of bin_width | Multiple of spread | Volatility-scaled
- **Answer: Fixed ticks** — Configurable fixed number

**Q: What's your preferred default value for N?**
- Options: 3 ticks | 5 ticks | 10 ticks | 1x spread equivalent
- **Answer: 1x spread equivalent** — Use typical spread as baseline

**Q: The minimum bin count for VA validity - what should the default threshold be?**
- Options: 5 bins | 10 bins | 20 bins | Relative to window
- **Answer: 20 bins** — Stricter requirement ensuring meaningful profile

### Acceptance Rules

**Q: What if there's a wick that briefly goes back inside during acceptance counting?**
- Options: Close only matters | Entire bar must stay out | Close with wick tolerance
- **Answer: Close only matters** — Only close price determines inside/outside, wicks ignored

**Q: For break-in signals, if the bar's low EXACTLY equals VAL (not strictly less), is it an excursion?**
- Options: Strict inequality | Inclusive | Configurable epsilon
- **Answer: Strict inequality** — low < VAL required (touching exactly doesn't count)

**Q: If consecutive closes are above different VAH values (because VAH shifted), does the sequence continue or reset?**
- Options: Use original VAH | Use current VAH | Use minimum VAH
- **Answer: Use original VAH** — Lock VAH at start of sequence, count against that level

**Q: How long should the original VAH be locked?**
- Options: Until complete/reset | Max duration cap | Rolling reassess
- **Answer: Until complete/reset** — Lock until k bars achieved or sequence broken

**Q: What's the tolerance if the acceptance sequence is broken?**
- Options: Hard reset on 1 close | Allow 1 break | Require consecutive | Configurable tolerance
- **Answer: Hard reset on 1 close** — Any close inside VA resets counter to 0

### Order Flow

**Q: If a single large trade is split into multiple fills at the same timestamp, how should they be handled?**
- Options: Aggregate same-ts | Treat separately | Aggregate within N ms
- **Answer: Aggregate same-ts** — Combine all trades at identical timestamp into one

**Q: Which should be used for signal thresholds: absolute OF, normalized OF, or both?**
- Options: Absolute OF only | Normalized OF only | Both with AND logic | Either/OR logic
- **Answer: Either/OR logic** — Can pass with either strong absolute OR strong normalized

**Q: The tick rule fallback variant - which should be used?**
- Options: Simple uptick/downtick | Zero-tick continuation | Lee-Ready style | Not implementing
- **Answer: Zero-tick continuation** — If price unchanged, use previous tick's sign

### Quote Imbalance

**Q: Should qimb use the snapshot at close, an average over last N seconds, or an EMA?**
- Options: Snapshot at close | Average last N seconds | EMA over minute | Both available
- **Answer: Both available** — Compute and store both raw and smoothed for analysis

**Q: What should the EMA span be?**
- Options: 10 seconds | 30 seconds | Full minute | Configurable parameter
- **Answer: Full minute** — Average over entire 1-minute bar

### Risk Management

**Q: Should cooldown apply globally or directionally?**
- Options: Global cooldown | Directional cooldown | Setup-specific
- **Answer: Global cooldown** — No new entries in either direction during cooldown

**Q: Should the max_hold_minutes time stop have different behavior for winning vs losing positions?**
- Options: Hard exit always | Extend if profitable | Exit losing only
- **Answer: Extend if profitable** — If position is in profit, allow extended hold

**Q: For 'extend if profitable', what's the extension logic?**
- Options: Fixed multiplier | Trail while profitable | Trailing with cap | Move to breakeven
- **Answer: Trail while profitable** — Keep extending as long as position stays profitable

**Q: What defines 'profitable' for trailing?**
- Options: Any P&L > 0 | Cover fees first | N ticks minimum | % of stop distance
- **Answer: Cover fees first** — Must be profitable net of estimated fees to extend

**Q: How should 'daily' be defined for the kill switch?**
- Options: UTC calendar day | Rolling 24h | Configurable reset time | 8h sessions
- **Answer: UTC calendar day** — Reset at 00:00 UTC each day

**Q: Should kill switch P&L include funding payments?**
- Options: Trade P&L only | Include funding | Separate thresholds
- **Answer: Include funding** — Net of all costs including funding (true economic P&L)

**Q: Should there be a maximum number of consecutive losing trades before halt?**
- Options: No streak limit | Fixed streak limit | Streak + cooldown | Configurable limit
- **Answer: No streak limit** — Only daily P&L kill switch applies

**Q: Are there any additional risk limits beyond daily loss?**
- Options: Daily loss only | Add max drawdown | Daily + weekly | Full risk suite
- **Answer: Daily loss only** — Only daily P&L kill switch as specified

### Position Sizing

**Q: How should position size be computed?**
- Options: Fixed configurable | Risk-based | Both available | Start fixed, add risk later
- **Answer: Risk-based** — position = (total_capital * risk%) / (entry - stop)
- **Additional detail:** Total capital times risk percentage, divided by entry minus stop-loss. This implies leverage may be needed if calculated position > actual capital. Max leverage is 10x (cross margin).

**Q: What should the default risk percentage be?**
- Options: 0.5% | 1% | 2% | Configurable default
- **Answer: 2%** — More aggressive sizing

**Q: When calculated leverage exceeds 10x, what should happen?**
- Options: Reduce to fit | Reject trade | Alert + reduce | Configurable behavior
- **Answer: Reduce to fit** — Scale down position to use exactly 10x leverage

**Q: For position sizing calculation, should 'total capital' be full account equity or available margin?**
- Options: Full account equity | Available margin | Fixed capital pool | Equity - drawdown buffer
- **Answer: Available margin** — Only use free margin not tied to positions

**Q: Should capital-based sizing be included in v0?**
- Options: Fixed 1-contract v0 | Include sizing v0 | Configurable fixed
- **Answer: Include sizing v0** — Implement capital-based sizing with partial exits in v0

### Partial Exits

**Q: Should the system support partial profit taking?**
- Options: All-in/all-out only | Support partial exits | Configurable split
- **Answer: Configurable split** — Parameter for TP1%/TP2% allocation

**Q: What should the default TP1/TP2 allocation be?**
- Options: 50/50 | 70/30 | 30/70 | 100/0 default
- **Answer: 30/70** — 30% at TP1, let majority run to TP2

**Q: If TP1 is hit and partial exit occurs, what should happen to the stop?**
- Options: Original stop | Move to breakeven | Tighten by N ticks | Configurable behavior
- **Answer: Move to breakeven** — Move stop to entry price after TP1 hit

**Q: Should TP1 exit a fixed % of position or a fixed contract count?**
- Options: Fixed percentage | Round to contracts | Minimum 1 contract | Skip if < 2 contracts
- **Answer: Round to contracts** — Exit 30% rounded to nearest whole contract

**Q: When rounding partial exits, should it round up, down, or to nearest?**
- Options: Round down | Round up | Round nearest | Minimum 1
- **Answer: Round nearest** — Standard rounding to nearest whole

**Q: If position is 1 contract and can't be split, what should happen?**
- Options: Skip partial for 1 contract | Full exit at TP2 | Use fractional if supported | Minimum 2 contracts for partial
- **Answer: Full exit at TP2** — Hold entire position for TP2 target

### Stops and Targets

**Q: Should targets be static (set at entry) or dynamic (adjust if VA shifts)?**
- Options: Static at entry | Dynamic tracking | Static with minimum
- **Answer: Static at entry** — Lock TP levels based on VA at entry time

**Q: For excursion-based stop calculation, should excursion be tracked over trigger bar only or a lookback window?**
- Options: Trigger bar only | Last N bars | Recent VA breach | Configurable window
- **Answer: Trigger bar only** — Stop based on the single bar that triggered the signal

**Q: For breakout stops, which condition takes priority - close back inside value, or below pullback low?**
- Options: Either triggers exit | Close inside priority | Pullback low only | Configurable mode
- **Answer: Either triggers exit** — First condition hit causes exit

**Q: When a position is opened, when does the max_hold_minutes timer start?**
- Options: Signal time | Fill time | Bar close after fill
- **Answer: Bar close after fill** — Timer starts at next bar close after confirmed fill

**Q: If TP and stop are hit in the same minute bar (whipsaw), what order of fills?**
- Options: Assume worst case | Use bar OHLC order | Check tick data | Full exit at mid
- **Answer: Assume worst case** — Assume stop hit first (conservative for backtesting)

### Execution

**Q: For limit entry orders, if not filled within N minutes, what should happen?**
- Options: Cancel after N min | Convert to market | Cancel if signal void | IOC style
- **Answer: Convert to market** — If not filled after N min, send market order

**Q: What should N minutes be for the limit timeout?**
- Options: 1 minute | 3 minutes | 5 minutes | Until bar close
- **Answer: 1 minute** — Quick conversion, prioritize getting filled

**Q: Should limit order support be included in v0?**
- Options: Market only for v0 | Include limit support | Limit for entries only | Configurable per setup
- **Answer: Limit for entries only** — Try limits for entries, market for exits

**Q: What should happen if exchange position doesn't match system state?**
- Options: Alert and halt | Trust exchange | Trust system | Configurable mode
- **Answer: Alert and halt** — Stop trading, require manual resolution

**Q: For live execution, should the system use REST API, WebSocket, or both?**
- Options: REST only | WebSocket only | WebSocket + REST fallback | Configurable
- **Answer: WebSocket + REST fallback** — WS primary, REST for reliability/reconciliation

**Q: What should the max backoff cap be for WebSocket reconnection?**
- Options: 30 seconds | 60 seconds | 5 minutes | Circuit breaker
- **Answer: 60 seconds** — Balanced approach

### Breakout Retest Mode

**Q: Should retest entry mode be included in v0?**
- Options: Immediate entry only | Include retest mode | Configurable toggle
- **Answer: Include retest mode** — Full retest entry logic with pullback detection

**Q: What defines a valid 'pullback' after breakout acceptance?**
- Options: Touch VAH exactly | Within N% of range | Within 1 bin_width | Configurable threshold
- **Answer: Configurable threshold** — Parameter for pullback depth

**Q: What should the default pullback threshold be?**
- Options: 2 ticks from VAH | 5 ticks from VAH | 0.5x bin_width | 1x typical spread
- **Answer: 1x typical spread** — Account for bid/ask when judging pullback depth

**Q: If the pullback happens but then price fails (closes back inside VA), what should happen?**
- Options: Triggers failed breakout | Retest expires quietly | Depends on OF
- **Answer: Triggers failed breakout** — Transitions to Setup C (fakeout) automatically

### Warmup and Startup

**Q: What's the minimum warmup period before generating signals?**
- Options: Full 240 minutes | Partial (e.g., 60 min) | Volume-based | Configurable threshold
- **Answer: Full 240 minutes** — Wait for complete 4h window before any trading

**Q: For live trading, should there be a warm-up period where signals are generated but not acted upon?**
- Options: No warmup | Configurable warmup | First session observe | Manual activation
- **Answer: Manual activation** — System starts in observe mode; operator enables trading

**Q: When the system restarts, should it attempt to reconstruct state from database?**
- Options: Auto-reconstruct | Require confirmation | Start flat always | Configurable mode
- **Answer: Auto-reconstruct** — Load state from DB and resume automatically

**Q: From which database(s) should state be reconstructed?**
- Options: Execution DB only | Signals + Execution | Full state snapshot | Minimal + recompute
- **Answer: Full state snapshot** — Periodic full state snapshots to dedicated table

**Q: How often should state snapshots be persisted?**
- Options: Every minute | Every 5 minutes | On state change only | On state change + periodic
- **Answer: Every 5 minutes** — Balance frequency vs disk I/O

### Mode Transitions

**Q: When transitioning from paper to live mode with an existing position, what should happen?**
- Options: Require flat | Replicate position | Independent tracking
- **Answer: Require flat** — Can only switch modes when no position is open

**Q: Should there be a 'shadow mode' where live signals are generated but only paper trades executed?**
- Options: Not needed | Include shadow mode | Post-v0
- **Answer: Include shadow mode** — Generate live signals, compare to paper, alert on divergence

**Q: For shadow mode, what should happen when paper and live signals diverge?**
- Options: Alert only | Alert + pause live | Log for analysis | Configurable response
- **Answer: Alert only** — Log divergence and send alert, continue running

**Q: Should there be a 'dry run' mode that processes data but doesn't even paper trade?**
- Options: Not needed | Include dry run | Signal-only mode
- **Answer: Include dry run** — Explicit mode that skips all execution logic

### Data and Storage

**Q: For the 48-hour raw data purge, how should deletion work?**
- Options: Hard cutoff | Rolling window | Batched (hourly) | Batched (daily)
- **Answer: Batched (daily)** — Purge once per day (simpler, slightly exceeds 48h)

**Q: What format do you expect historical data to be in?**
- Options: CSV files | Parquet | Database dump | Unknown/varies
- **Answer: CSV files** — Standard CSV with timestamp, price, size columns

**Q: Should the system include utilities for data format conversion/normalization?**
- Options: Assume correct schema | Include converters | Validation + rejection | Flexible ingestion
- **Answer: Include converters** — Built-in tools to convert common formats

**Q: For the auto-detect timestamp converter, if detection fails, what should happen?**
- Options: Error and stop | Prompt for format | Best effort | Skip bad rows
- **Answer: Best effort** — Try common formats, warn if ambiguous

**Q: For 'typical spread' calculation, should it be computed dynamically or use a fixed estimate?**
- Options: Rolling average | Fixed config | Percentile-based | Dynamic with floor
- **Answer: Rolling average** — EMA of spread over last N minutes

**Q: What lookback period for the rolling spread average?**
- Options: 10 minutes | 60 minutes | 240 minutes | Configurable
- **Answer: 60 minutes** — Last hour average

**Q: When computing sigma_240, what should happen with missing bars (no trades in a minute)?**
- Options: Skip missing | Zero return | Carry forward | Interpolate
- **Answer: Zero return** — Treat as r_t = 0 (no price change)

**Q: For the bid/ask snapshot at minute close, what if there's no quote in the last few seconds?**
- Options: Use last available | Mark as stale | Use trade prices | Invalidate bar
- **Answer: Use last available** — Use most recent quote regardless of age

**Q: When quote data becomes stale (>250ms since last update), what should happen?**
- Options: Use stale quote | Mark ambiguous | Halt signal processing | Graceful degradation
- **Answer: Use stale quote** — Classify with old quote, accept potential error

### Database Architecture

**Q: Should there be a single database file or separate databases for different concerns?**
- Options: Single database | Separate live/backtest | Separate by data type | Hybrid
- **Answer: Separate by data type** — Raw data, features, signals, trades in separate DBs

**Q: What's the preferred split for the separate databases?**
- Options: 3 DBs: raw/features/execution | 4 DBs: raw/features/signals/execution | 2 DBs: ephemeral/persistent | Per-run isolation
- **Answer: 4 DBs: raw/features/signals/execution** — Further split features from signals

**Q: Should the DBs be in a single directory or configurable locations?**
- Options: Single directory | Configurable per DB | Auto-tiering
- **Answer: Single directory** — All DBs in one configured data directory

### Backtesting

**Q: When backtesting, how should funding be modeled?**
- Options: Fixed average rate | Replay historical rates | Ignore funding | Configurable flat rate
- **Answer: Fixed average rate** — Use constant estimated funding rate (e.g., 0.01% per 8h)

**Q: For live mode, should the system fetch actual funding rates?**
- Options: Fixed rate always | Fetch for live only | Fetch and store | Configurable source
- **Answer: Fetch for live only** — Fixed rate for backtest, live rates for live trading

**Q: Should performance analytics be computed globally, per-setup, or both?**
- Options: Global only | Per-setup only | Both with comparison | Per-setup + per-direction
- **Answer: Global only** — Single aggregate P&L and metrics for all trades

**Q: Should the system support multiple concurrent backtest runs (parallelization)?**
- Options: Single-threaded | Multi-threaded | Multi-process | Configurable workers
- **Answer: Configurable workers** — User specifies number of parallel workers

**Q: What's the reasonable default number of workers?**
- Options: 1 (sequential) | CPU cores / 2 | CPU cores - 1 | Auto-detect
- **Answer: CPU cores / 2** — Leave headroom for system

**Q: Should there be a timeout per backtest run?**
- Options: No timeout | Per-run timeout | Total grid timeout | Resource-based
- **Answer: No timeout** — Each param combo runs to completion

**Q: Should the system support walk-forward backtests against live data?**
- Options: Historical only | Walk-forward supported | Unified engine | Out of scope v0
- **Answer: Historical only** — Backtest engine for historical data; paper mode is separate

### Grid Search

**Q: Beyond alpha_bin, which parameters should be sweep-able in v0?**
- Options: Core VA params | Signal thresholds | Risk params | All configurable params
- **Answer: All configurable params** — Everything in the config block should be sweepable

**Q: For grid search output, what's the primary result format?**
- Options: CSV per run | Summary table | Both CSV + summary | Database storage
- **Answer: Database storage** — Store all results in SQLite for flexible querying

**Q: Should grid search results be viewable in the web dashboard?**
- Options: Web dashboard | CLI + files | Jupyter notebook | Both web + CLI
- **Answer: Both web + CLI** — Web for quick comparison, CLI for detailed analysis

### Monitoring and Alerts

**Q: What notification channels should be supported?**
- Options: Slack only | Slack + Email | Webhook generic | Multiple configurable
- **Answer: Webhook generic** — Generic webhook that user can route anywhere

**Q: What's the priority ranking of alert types?**
- Options: Kill switch > Data > Position > Signal | Data > Kill switch > Position > Signal | Position > Kill switch > Data > Signal | All equal severity
- **Answer: All equal severity** — Any alert should trigger same notification level

**Q: Should the ambiguous_trade_frac_1m quality metric be displayed prominently?**
- Options: Dashboard prominent | Dashboard secondary | Log only | Alert threshold only
- **Answer: Dashboard prominent** — Show in main view with color-coded warning thresholds

**Q: Should the system track and display 'heat' (current risk exposure)?**
- Options: Yes, in dashboard | Log only | Not needed | Alert on threshold
- **Answer: Yes, in dashboard** — Show current risk exposure prominently

**Q: Should full feature snapshots be logged for every minute, or only when signals fire?**
- Options: Every minute | Signals only | Signals + near-miss | Configurable level
- **Answer: Every minute** — Full feature state logged each minute (larger storage)

### Dashboard and UI

**Q: For monitoring, should there be a real-time dashboard/UI?**
- Options: Logs + alerts only | Basic CLI dashboard | Web dashboard | Grafana/external
- **Answer: Web dashboard** — Browser-based real-time visualization

**Q: What framework preference for the web dashboard?**
- Options: FastAPI + htmx | FastAPI + React | Streamlit/Gradio | No preference
- **Answer: No preference** — Whatever gets the job done cleanly

**Q: What key visualizations are must-haves for v0?**
- Options: P&L + positions | VA visualization | Full trading view | Minimal status
- **Answer: Full trading view** — P&L + VA + signals + order flow metrics

**Q: For VA display, horizontal histogram (TPO) or colored price levels on chart?**
- Options: Horizontal histogram | Colored levels | Both views | Histogram with lines
- **Answer: Both views** — Tabbed/toggled between histogram and lines view

**Q: For the web dashboard, should it require authentication?**
- Options: No auth (local only) | Basic auth | Token-based | Full auth system
- **Answer: No auth (local only)** — Assume dashboard is only accessible on local network/VPN

### Tech Stack

**Q: What language/stack preference for implementation?**
- Options: Python (async) | Python (sync) | Rust | Python + Rust hybrid
- **Answer: Python + Rust hybrid** — Python orchestration, Rust for hot paths

**Q: For the Python + Rust hybrid, which components should be in Rust?**
- Options: Data ingestion + histogram | Full feature engine | Backtest engine | Ingestion + feature + backtest
- **Answer: Ingestion + feature + backtest** — All performance-critical paths; Python for orchestration/signals

**Q: For Rust-Python interop, what binding approach?**
- Options: PyO3 | FFI + ctypes | Arrow IPC | gRPC/subprocess
- **Answer: PyO3** — Native Python extension with PyO3

**Q: For the Rust components, single crate or multiple crates?**
- Options: Single crate | Workspace with 3 crates | Workspace with shared core | No preference
- **Answer: No preference** — Whatever makes sense architecturally

### Testing

**Q: Should the codebase include automated tests, and what coverage priority?**
- Options: Minimal unit tests | Comprehensive unit | Unit + integration | Test as needed
- **Answer: Comprehensive unit** — High coverage of all business logic

**Q: Should the spec include example calculations?**
- Options: Yes, detailed examples | Pseudocode only | Reference test cases | No examples needed
- **Answer: Yes, detailed examples** — Include numerical examples for key algorithms

### Configuration

**Q: Should config be stored in YAML, environment variables, database, or combination?**
- Options: Single YAML file | YAML + env vars | Hierarchical config | Database + file
- **Answer: YAML + env vars** — Secrets in env vars, rest in YAML

**Q: Which items should be in env vars (secrets)?**
- Options: API keys only | Keys + DB paths | Keys + webhook URLs | All sensitive
- **Answer: All sensitive** — API keys, webhook URLs, any potential PII/secrets

### Bybit Integration

**Q: Are you planning to use the unified trading account API or legacy derivatives API?**
- Options: Unified Trading (v5) | Legacy derivatives | Support both | Not sure yet
- **Answer: Unified Trading (v5)** — Latest unified account API

### BVC Fallback

**Q: Under what condition should BVC automatically engage?**
- Options: Manual only | High ambiguous fraction | Sustained quality issues | Always computed
- **Answer: Manual only** — BVC only used if explicitly configured; never auto-engaged

### Flip-on-Signal

**Q: Should flip-on-signal be enabled by default?**
- Options: Disabled by default | Enabled by default | Per-setup configurable
- **Answer: Enabled by default** — Flip allowed when opposite signal fires

**Q: If flip is enabled, should it count as one trade or two trades for analytics?**
- Options: One trade (net) | Two trades | One exit + one entry
- **Answer: Two trades** — Close existing + open new counted separately

### Paper Trading

**Q: Should paper trading use simulated fills or try to match real order book state?**
- Options: Simulated fills | Order book aware | Same as backtest | Configurable realism
- **Answer: Same as backtest** — Use identical fill model as backtest engine

### Multi-Strategy

**Q: Should the system support multiple strategies running simultaneously?**
- Options: Single strategy | Multiple strategies | A/B testing mode | Post-v0 feature
- **Answer: Post-v0 feature** — Multi-strategy is enhancement for later versions

### Project Naming

**Q: What naming convention preference for the project?**
- Options: btc-pbd-system | auction-trader | bybit-perp-engine | Custom name
- **Answer: auction-trader** — References AMT/auction theory

### Existing Code Integration

**Q: Should the TradeCore backtester be enhanced or should we build new?**
- Options: Enhance TradeCore | Build new in Rust | Phased approach | Hybrid
- **Answer: Build new in Rust** — Ignore TradeCore backtester, implement spec'd Rust version

**Q: For storage backend, TimescaleDB or DuckDB?**
- Options: TimescaleDB | DuckDB + Parquet | SQLite for all 4 DBs | Hybrid
- **Answer: (Deferred to Claude's recommendation)** — Chose DuckDB for raw/features (fast analytics), SQLite for signals/execution (simpler state management)

**Q: Should we reuse TradeCore's frontend charting code?**
- Options: Yes, reuse | Build fresh | Adapt and extend
- **Answer: Yes, reuse TradeCore charts** — Use total-core-v2.js footprint visualization as base

**Q: Should we port Total-Trader's WebSocket service to Rust or keep in Python?**
- Options: Port to Rust | Keep Python WS | Start Python, migrate later
- **Answer: Start Python, migrate later** — Use Python WS initially, Rust migration is future task

---

## Part 5: Summary of Key Numbers

| Parameter | Value | Source Question |
|-----------|-------|-----------------|
| Rolling window | 240 minutes | Spec default |
| VA fraction | 70% | Spec default |
| Min VA bins | 20 | Design Q&A |
| Acceptance k | 3 | Spec default |
| Risk per trade | 2% | Design Q&A |
| Max leverage | 10x | Design Q&A |
| TP1 allocation | 30% | Design Q&A |
| TP2 allocation | 70% | Design Q&A |
| Cooldown | 3 minutes | Spec default |
| Max hold base | 60 minutes | Spec default |
| Quote staleness | 250 ms | Spec default |
| Ambiguous threshold | 35% | Spec default |
| Limit order timeout | 1 minute | Design Q&A |
| Spread lookback | 60 minutes | Design Q&A |
| Snapshot interval | 5 minutes | Design Q&A |
| WS max backoff | 60 seconds | Design Q&A |
| Default workers | CPU/2 | Design Q&A |

---

*Document generated from specification interview conducted January 2026*
