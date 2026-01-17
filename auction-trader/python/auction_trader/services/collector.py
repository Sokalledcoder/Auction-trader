"""WebSocket collector for Bybit market data.

Subscribes to:
- publicTrade: Real-time trades
- orderbook.1: Level 1 quotes (best bid/ask)

Converts raw messages to internal Trade/Quote types and feeds
to the processing pipeline.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Optional, Callable, Awaitable, List
from datetime import datetime
import websockets
from websockets.exceptions import ConnectionClosed

from ..config import Config
from ..models.types import Trade, Quote

logger = logging.getLogger(__name__)


# Bybit WebSocket endpoints
BYBIT_WS_MAINNET = "wss://stream.bybit.com/v5/public/linear"
BYBIT_WS_TESTNET = "wss://stream-testnet.bybit.com/v5/public/linear"


@dataclass
class CollectorStats:
    """Statistics for the collector."""
    trades_received: int = 0
    quotes_received: int = 0
    reconnections: int = 0
    errors: int = 0
    last_trade_ts: Optional[int] = None
    last_quote_ts: Optional[int] = None


TradeCallback = Callable[[Trade], Awaitable[None]]
QuoteCallback = Callable[[Quote], Awaitable[None]]


class BybitCollector:
    """WebSocket collector for Bybit perpetual futures.

    Connects to Bybit's public WebSocket and streams trades and L1 quotes.

    Usage:
        collector = BybitCollector(config)
        collector.on_trade = async_trade_handler
        collector.on_quote = async_quote_handler
        await collector.run()
    """

    def __init__(
        self,
        config: Config,
        use_testnet: bool = False,
    ):
        self.config = config
        self.symbol = config.instrument.symbol
        self.use_testnet = use_testnet

        # Callbacks
        self.on_trade: Optional[TradeCallback] = None
        self.on_quote: Optional[QuoteCallback] = None

        # Connection state
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._running = False
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 60.0

        # Stats
        self.stats = CollectorStats()

        # Latest quote for reference
        self._latest_quote: Optional[Quote] = None

    @property
    def ws_url(self) -> str:
        """Get WebSocket URL based on testnet setting."""
        return BYBIT_WS_TESTNET if self.use_testnet else BYBIT_WS_MAINNET

    async def run(self) -> None:
        """Run the collector (blocking)."""
        self._running = True
        logger.info(f"Starting Bybit collector for {self.symbol}")

        while self._running:
            try:
                await self._connect_and_stream()
            except ConnectionClosed as e:
                logger.warning(f"WebSocket connection closed: {e}")
                self.stats.reconnections += 1
            except Exception as e:
                logger.error(f"Collector error: {e}")
                self.stats.errors += 1

            if self._running:
                logger.info(f"Reconnecting in {self._reconnect_delay}s...")
                await asyncio.sleep(self._reconnect_delay)
                # Exponential backoff
                self._reconnect_delay = min(
                    self._reconnect_delay * 2,
                    self._max_reconnect_delay
                )

    async def stop(self) -> None:
        """Stop the collector."""
        self._running = False
        if self._ws:
            await self._ws.close()

    async def _connect_and_stream(self) -> None:
        """Connect to WebSocket and stream data."""
        async with websockets.connect(
            self.ws_url,
            ping_interval=20,
            ping_timeout=10,
        ) as ws:
            self._ws = ws
            logger.info(f"Connected to {self.ws_url}")

            # Reset reconnect delay on successful connection
            self._reconnect_delay = 1.0

            # Subscribe to channels
            await self._subscribe(ws)

            # Process messages
            async for message in ws:
                await self._handle_message(message)

    async def _subscribe(self, ws: websockets.WebSocketClientProtocol) -> None:
        """Subscribe to trade and orderbook channels."""
        # Subscribe to trades
        trade_sub = {
            "op": "subscribe",
            "args": [f"publicTrade.{self.symbol}"]
        }
        await ws.send(json.dumps(trade_sub))
        logger.info(f"Subscribed to publicTrade.{self.symbol}")

        # Subscribe to L1 orderbook (best bid/ask)
        book_sub = {
            "op": "subscribe",
            "args": [f"orderbook.1.{self.symbol}"]
        }
        await ws.send(json.dumps(book_sub))
        logger.info(f"Subscribed to orderbook.1.{self.symbol}")

    async def _handle_message(self, raw_message: str) -> None:
        """Handle incoming WebSocket message."""
        try:
            msg = json.loads(raw_message)
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse message: {raw_message[:100]}")
            return

        # Skip subscription confirmations and pings
        if "success" in msg or "op" in msg:
            return

        topic = msg.get("topic", "")

        if topic.startswith("publicTrade."):
            await self._handle_trades(msg)
        elif topic.startswith("orderbook."):
            await self._handle_orderbook(msg)

    async def _handle_trades(self, msg: dict) -> None:
        """Handle public trade messages."""
        data = msg.get("data", [])

        for trade_data in data:
            try:
                trade = self._parse_trade(trade_data)
                self.stats.trades_received += 1
                self.stats.last_trade_ts = trade.ts_ms

                if self.on_trade:
                    await self.on_trade(trade)

            except Exception as e:
                logger.warning(f"Failed to parse trade: {e}")
                self.stats.errors += 1

    async def _handle_orderbook(self, msg: dict) -> None:
        """Handle orderbook (L1 quote) messages."""
        data = msg.get("data", {})

        try:
            quote = self._parse_orderbook(data)
            if quote:
                self._latest_quote = quote
                self.stats.quotes_received += 1
                self.stats.last_quote_ts = quote.ts_ms

                if self.on_quote:
                    await self.on_quote(quote)

        except Exception as e:
            logger.warning(f"Failed to parse orderbook: {e}")
            self.stats.errors += 1

    def _parse_trade(self, data: dict) -> Trade:
        """Parse Bybit trade message to Trade object.

        Bybit trade format:
        {
            "T": 1672052399000,  # timestamp ms
            "s": "BTCUSDT",      # symbol
            "S": "Buy",         # side
            "v": "0.001",       # quantity
            "p": "16578.50",    # price
            "L": "PlusTick",    # tick direction
            "i": "abc123",      # trade id
            "BT": false         # is block trade
        }
        """
        return Trade(
            ts_ms=int(data["T"]),
            price=float(data["p"]),
            size=float(data["v"]),
        )

    def _parse_orderbook(self, data: dict) -> Optional[Quote]:
        """Parse Bybit orderbook message to Quote object.

        Bybit L1 orderbook format:
        {
            "s": "BTCUSDT",
            "b": [["16578.50", "1.5"]],  # bids [[price, size], ...]
            "a": [["16578.60", "2.0"]],  # asks [[price, size], ...]
            "u": 12345,                   # update id
            "seq": 67890                  # sequence
        }
        """
        bids = data.get("b", [])
        asks = data.get("a", [])

        if not bids or not asks:
            return None

        # Get best bid and ask
        best_bid = bids[0]
        best_ask = asks[0]

        # Use message timestamp or current time
        ts_ms = data.get("ts", int(datetime.now().timestamp() * 1000))

        return Quote(
            ts_ms=ts_ms,
            bid_px=float(best_bid[0]),
            bid_sz=float(best_bid[1]),
            ask_px=float(best_ask[0]),
            ask_sz=float(best_ask[1]),
        )

    @property
    def latest_quote(self) -> Optional[Quote]:
        """Get the most recent quote."""
        return self._latest_quote

    def get_stats_dict(self) -> dict:
        """Get stats as dictionary."""
        return {
            "trades_received": self.stats.trades_received,
            "quotes_received": self.stats.quotes_received,
            "reconnections": self.stats.reconnections,
            "errors": self.stats.errors,
            "last_trade_ts": self.stats.last_trade_ts,
            "last_quote_ts": self.stats.last_quote_ts,
        }


class MockCollector:
    """Mock collector for testing and backtesting.

    Allows feeding trades and quotes manually.
    """

    def __init__(self, config: Config):
        self.config = config
        self.on_trade: Optional[TradeCallback] = None
        self.on_quote: Optional[QuoteCallback] = None
        self.stats = CollectorStats()

    async def feed_trade(self, trade: Trade) -> None:
        """Feed a trade to the pipeline."""
        self.stats.trades_received += 1
        self.stats.last_trade_ts = trade.ts_ms
        if self.on_trade:
            await self.on_trade(trade)

    async def feed_quote(self, quote: Quote) -> None:
        """Feed a quote to the pipeline."""
        self.stats.quotes_received += 1
        self.stats.last_quote_ts = quote.ts_ms
        if self.on_quote:
            await self.on_quote(quote)

    async def feed_trades(self, trades: List[Trade]) -> None:
        """Feed multiple trades."""
        for trade in trades:
            await self.feed_trade(trade)

    async def feed_quotes(self, quotes: List[Quote]) -> None:
        """Feed multiple quotes."""
        for quote in quotes:
            await self.feed_quote(quote)
