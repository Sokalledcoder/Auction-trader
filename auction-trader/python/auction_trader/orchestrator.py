"""Main orchestrator for the auction-trader system.

Coordinates:
- Data collection (WebSocket)
- Feature computation
- Signal generation
- Order execution
- State persistence
"""

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Optional, Callable, Awaitable
from datetime import datetime
from pathlib import Path

from .config import Config, load_config
from .models.types import (
    Trade,
    Quote,
    Bar1m,
    Features1m,
    Signal,
    Action,
    ValueArea,
    OrderFlowMetrics,
    ts_to_minute,
    current_ts_ms,
)
from .services.collector import BybitCollector, MockCollector
from .services.signal_engine import SignalEngine
from .services.position_manager import PositionManager
from .services.execution import BybitExecutor, PaperExecutor

logger = logging.getLogger(__name__)


class TradingMode:
    """Trading mode constants."""
    PAPER = "paper"
    LIVE = "live"
    SHADOW = "shadow"
    BACKTEST = "backtest"


@dataclass
class OrchestratorState:
    """Current state of the orchestrator."""
    is_running: bool = False
    current_bar: Optional[Bar1m] = None
    current_features: Optional[Features1m] = None
    last_signal: Optional[Signal] = None
    bars_processed: int = 0
    signals_generated: int = 0
    trades_executed: int = 0


class Orchestrator:
    """Main trading system orchestrator.

    Modes:
    - paper: Simulated trading with real market data
    - live: Real trading with real money
    - shadow: Track signals without executing
    - backtest: Historical simulation
    """

    def __init__(
        self,
        config: Config,
        mode: str = TradingMode.PAPER,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        use_testnet: bool = True,
    ):
        self.config = config
        self.mode = mode

        # Initialize components based on mode
        if mode == TradingMode.BACKTEST:
            self.collector = MockCollector(config)
            self.executor = PaperExecutor(config)
        elif mode == TradingMode.PAPER:
            self.collector = BybitCollector(config, use_testnet=use_testnet)
            self.executor = PaperExecutor(config)
        elif mode == TradingMode.LIVE:
            if not api_key or not api_secret:
                raise ValueError("API credentials required for live trading")
            self.collector = BybitCollector(config, use_testnet=use_testnet)
            self.executor = BybitExecutor(
                config, api_key, api_secret, use_testnet=use_testnet
            )
        else:  # shadow
            self.collector = BybitCollector(config, use_testnet=use_testnet)
            self.executor = PaperExecutor(config)

        # Core engines
        self.signal_engine = SignalEngine(config)
        self.position_manager = PositionManager(
            config, config.backtest.initial_capital
        )

        # State
        self.state = OrchestratorState()

        # Bar accumulation
        self._trade_buffer: list[Trade] = []
        self._latest_quote: Optional[Quote] = None
        self._current_bar_start: Optional[int] = None

        # Feature computation state (simplified - would use Rust in production)
        self._bar_history: list[Bar1m] = []
        self._max_history = config.instrument.rolling_window_minutes + 60

        # Callbacks for external monitoring
        self.on_bar: Optional[Callable[[Bar1m], Awaitable[None]]] = None
        self.on_features: Optional[Callable[[Features1m], Awaitable[None]]] = None
        self.on_signal: Optional[Callable[[Signal], Awaitable[None]]] = None

    async def start(self) -> None:
        """Start the orchestrator."""
        logger.info(f"Starting orchestrator in {self.mode} mode")

        # Start executor
        await self.executor.start()

        # Set up collector callbacks
        self.collector.on_trade = self._on_trade
        self.collector.on_quote = self._on_quote

        self.state.is_running = True

        # Start collector (blocking)
        if self.mode != TradingMode.BACKTEST:
            await self.collector.run()

    async def stop(self) -> None:
        """Stop the orchestrator."""
        logger.info("Stopping orchestrator")
        self.state.is_running = False

        if hasattr(self.collector, 'stop'):
            await self.collector.stop()
        await self.executor.stop()

    async def _on_trade(self, trade: Trade) -> None:
        """Handle incoming trade."""
        trade_minute = ts_to_minute(trade.ts_ms)

        # Check if we need to finalize previous bar
        if self._current_bar_start is not None and trade_minute > self._current_bar_start:
            await self._finalize_bar()

        # Start new bar if needed
        if self._current_bar_start is None or trade_minute > self._current_bar_start:
            self._current_bar_start = trade_minute
            self._trade_buffer = []

        self._trade_buffer.append(trade)

    async def _on_quote(self, quote: Quote) -> None:
        """Handle incoming quote."""
        self._latest_quote = quote

        # Check position exits on each quote
        if self.position_manager.has_position:
            await self._check_position_exits(quote)

    async def _finalize_bar(self) -> None:
        """Finalize the current bar and process it."""
        if not self._trade_buffer or self._latest_quote is None:
            return

        # Build bar from trades
        bar = self._build_bar()
        if bar is None:
            return

        self._bar_history.append(bar)
        if len(self._bar_history) > self._max_history:
            self._bar_history = self._bar_history[-self._max_history:]

        self.state.current_bar = bar
        self.state.bars_processed += 1

        # Notify listeners
        if self.on_bar:
            await self.on_bar(bar)

        # Compute features
        features = self._compute_features(bar)
        self.state.current_features = features

        if self.on_features:
            await self.on_features(features)

        # Generate signal
        signal = self.signal_engine.process(features)
        self.state.last_signal = signal

        if signal.action != Action.HOLD:
            self.state.signals_generated += 1

            if self.on_signal:
                await self.on_signal(signal)

            # Execute signal (unless shadow mode)
            if self.mode != TradingMode.SHADOW:
                await self._execute_signal(signal)

    def _build_bar(self) -> Optional[Bar1m]:
        """Build a 1-minute bar from buffered trades."""
        if not self._trade_buffer or self._latest_quote is None:
            return None

        trades = self._trade_buffer
        quote = self._latest_quote

        prices = [t.price for t in trades]
        volumes = [t.size for t in trades]
        total_volume = sum(volumes)

        if total_volume == 0:
            return None

        # Calculate VWAP
        vwap = sum(t.price * t.size for t in trades) / total_volume

        return Bar1m(
            ts_min=self._current_bar_start,
            open=prices[0],
            high=max(prices),
            low=min(prices),
            close=prices[-1],
            volume=total_volume,
            vwap=vwap,
            trade_count=len(trades),
            bid_px_close=quote.bid_px,
            ask_px_close=quote.ask_px,
            bid_sz_close=quote.bid_sz,
            ask_sz_close=quote.ask_sz,
        )

    def _compute_rolling_volatility(self, bars: list[Bar1m], default: float = 0.01) -> float:
        """Compute rolling volatility from bar returns."""
        if len(bars) < 2:
            return default
        returns = [
            (bars[i].close - bars[i - 1].close) / bars[i - 1].close
            for i in range(1, len(bars))
            if bars[i - 1].close > 0
        ]
        if not returns:
            return default
        import math
        mean_ret = sum(returns) / len(returns)
        variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
        return math.sqrt(variance) if variance > 0 else default

    def _compute_features(self, bar: Bar1m) -> Features1m:
        """Compute features for the current bar.

        This is a simplified Python implementation.
        Production would use the Rust FeatureEngine via PyO3.
        """
        window = self.config.instrument.rolling_window_minutes
        recent_bars = self._bar_history[-window:]

        sigma_240 = self._compute_rolling_volatility(recent_bars)

        # Calculate adaptive bin width
        tick_size = self.config.instrument.tick_size
        alpha = self.config.value_area.alpha_bin
        bin_width = max(
            tick_size,
            min(sigma_240 * alpha * bar.close, tick_size * self.config.value_area.bin_width_max_ticks),
        )

        va = self._compute_value_area(recent_bars, bin_width)
        order_flow = self._compute_order_flow(bar)

        # QIMB EMA (simplified - would use proper EMA in production)
        qimb_close = bar.qimb_close
        qimb_ema = qimb_close

        # Calculate spread average over last 60 bars
        spread_bars = recent_bars[-60:] if recent_bars else []
        spread_avg = sum(b.spread_close for b in spread_bars) / len(spread_bars) if spread_bars else 0.0

        return Features1m(
            ts_min=bar.ts_min,
            mid_close=bar.mid_close,
            sigma_240=sigma_240,
            bin_width=bin_width,
            va=va,
            order_flow=order_flow,
            qimb_close=qimb_close,
            qimb_ema=qimb_ema,
            spread_avg_60m=spread_avg,
        )

    def _compute_value_area(self, bars: list[Bar1m], bin_width: float) -> ValueArea:
        """Compute Value Area from price/volume data."""
        if not bars or bin_width <= 0:
            return ValueArea.invalid()

        # Build volume profile
        volume_profile: dict[int, float] = {}
        total_volume = 0.0

        for bar in bars:
            # Use VWAP as representative price
            price = bar.vwap if bar.vwap else bar.close
            bin_idx = int(price / bin_width)

            volume_profile[bin_idx] = volume_profile.get(bin_idx, 0) + bar.volume
            total_volume += bar.volume

        if total_volume == 0 or len(volume_profile) < self.config.value_area.min_va_bins:
            return ValueArea.invalid()

        # Find POC (Point of Control)
        poc_bin = max(volume_profile.keys(), key=lambda k: volume_profile[k])
        poc = (poc_bin + 0.5) * bin_width

        # Expand from POC to find VA (70% of volume)
        va_fraction = self.config.value_area.va_fraction
        target_volume = total_volume * va_fraction

        va_bins = {poc_bin}
        current_volume = volume_profile[poc_bin]

        # Get sorted bin indices
        sorted_bins = sorted(volume_profile.keys())
        poc_idx = sorted_bins.index(poc_bin)

        upper_idx = poc_idx + 1
        lower_idx = poc_idx - 1

        while current_volume < target_volume:
            upper_vol = 0.0
            lower_vol = 0.0

            if upper_idx < len(sorted_bins):
                upper_vol = volume_profile.get(sorted_bins[upper_idx], 0)
            if lower_idx >= 0:
                lower_vol = volume_profile.get(sorted_bins[lower_idx], 0)

            if upper_vol == 0 and lower_vol == 0:
                break

            if upper_vol >= lower_vol:
                if upper_idx < len(sorted_bins):
                    va_bins.add(sorted_bins[upper_idx])
                    current_volume += upper_vol
                    upper_idx += 1
            else:
                if lower_idx >= 0:
                    va_bins.add(sorted_bins[lower_idx])
                    current_volume += lower_vol
                    lower_idx -= 1

        # Calculate VAH and VAL
        vah_bin = max(va_bins)
        val_bin = min(va_bins)

        vah = (vah_bin + 1) * bin_width
        val = val_bin * bin_width

        return ValueArea(
            poc=poc,
            vah=vah,
            val=val,
            coverage=current_volume / total_volume if total_volume > 0 else 0,
            bin_count=len(va_bins),
            total_volume=total_volume,
            bin_width=bin_width,
            is_valid=True,
        )

    def _compute_order_flow(self, bar: Bar1m) -> OrderFlowMetrics:
        """Compute simplified order flow metrics."""
        # In production, this would use classified trades from Rust
        # Here we use a simplified approximation based on bar data

        # Estimate buy/sell from close position in bar range
        bar_range = bar.high - bar.low
        if bar_range > 0:
            close_position = (bar.close - bar.low) / bar_range  # 0 to 1
        else:
            close_position = 0.5

        buy_volume = bar.volume * close_position
        sell_volume = bar.volume * (1 - close_position)
        ambiguous_volume = 0.0  # Simplified

        of_raw = buy_volume - sell_volume
        of_norm = of_raw / bar.volume if bar.volume > 0 else 0

        return OrderFlowMetrics(
            of_1m=of_raw,
            of_norm_1m=of_norm,
            total_volume=bar.volume,
            buy_volume=buy_volume,
            sell_volume=sell_volume,
            ambiguous_volume=ambiguous_volume,
            ambiguous_frac=0.0,
        )

    async def _execute_signal(self, signal: Signal) -> None:
        """Execute a trading signal."""
        if signal.action == Action.HOLD:
            return

        quote = self._latest_quote
        if quote is None:
            logger.warning("No quote available for execution")
            return

        current_price = quote.mid
        current_ts = current_ts_ms()

        # Process through position manager
        result = self.position_manager.process_signal(
            signal, current_price, current_ts
        )

        if result:
            logger.info(f"Position manager: {result}")
            self.state.trades_executed += 1

    async def _check_position_exits(self, quote: Quote) -> None:
        """Check for position exits."""
        if not self.position_manager.has_position:
            return

        current_ts = current_ts_ms()

        # Use quote spread as proxy for high/low
        result = self.position_manager.check_exits(
            high=quote.ask_px,
            low=quote.bid_px,
            current_price=quote.mid,
            current_ts=current_ts,
        )

        if result:
            logger.info(f"Position exit: {result}")

    def get_stats(self) -> dict:
        """Get orchestrator statistics."""
        return {
            "mode": self.mode,
            "is_running": self.state.is_running,
            "bars_processed": self.state.bars_processed,
            "signals_generated": self.state.signals_generated,
            "trades_executed": self.state.trades_executed,
            "position_stats": self.position_manager.get_stats(),
            "collector_stats": self.collector.get_stats_dict() if hasattr(self.collector, 'get_stats_dict') else {},
        }

    def reset(self) -> None:
        """Reset orchestrator state."""
        self.state = OrchestratorState()
        self._trade_buffer = []
        self._latest_quote = None
        self._current_bar_start = None
        self._bar_history = []
        self.signal_engine.reset()
        self.position_manager.reset()
