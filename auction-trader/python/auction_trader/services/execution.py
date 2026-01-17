"""Execution engine for order management via Bybit API.

Handles:
- Order placement (limit and market)
- Order cancellation
- Position queries
- Limit order timeout and conversion to market
"""

import asyncio
import logging
import hmac
import hashlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any
from enum import Enum, auto
import aiohttp

from ..config import Config
from ..models.types import PositionSide, Quote

logger = logging.getLogger(__name__)


# Bybit API endpoints
BYBIT_API_MAINNET = "https://api.bybit.com"
BYBIT_API_TESTNET = "https://api-testnet.bybit.com"


class OrderStatus(Enum):
    """Order status."""
    PENDING = auto()
    NEW = auto()
    PARTIALLY_FILLED = auto()
    FILLED = auto()
    CANCELLED = auto()
    REJECTED = auto()
    EXPIRED = auto()


class OrderType(Enum):
    """Order type."""
    MARKET = "Market"
    LIMIT = "Limit"


class OrderSide(Enum):
    """Order side."""
    BUY = "Buy"
    SELL = "Sell"


@dataclass
class Order:
    """Order tracking."""
    order_id: str
    client_order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    price: Optional[float]
    qty: float
    status: OrderStatus = OrderStatus.PENDING
    filled_qty: float = 0.0
    avg_price: float = 0.0
    created_at: int = 0
    updated_at: int = 0


@dataclass
class ExecutionResult:
    """Result of an execution attempt."""
    success: bool
    order: Optional[Order] = None
    error: Optional[str] = None
    filled_price: Optional[float] = None
    filled_qty: Optional[float] = None


class BybitExecutor:
    """Execution engine for Bybit perpetual futures.

    Implements:
    - Limit order entry with timeout
    - Market order fallback
    - Position management
    - Order tracking
    """

    def __init__(
        self,
        config: Config,
        api_key: str,
        api_secret: str,
        use_testnet: bool = False,
    ):
        self.config = config
        self.execution_config = config.execution
        self.symbol = config.instrument.symbol
        self.tick_size = config.instrument.tick_size

        self.api_key = api_key
        self.api_secret = api_secret
        self.use_testnet = use_testnet

        # Session for API calls
        self._session: Optional[aiohttp.ClientSession] = None

        # Order tracking
        self._orders: Dict[str, Order] = {}
        self._pending_limit_orders: Dict[str, asyncio.Task] = {}

    @property
    def base_url(self) -> str:
        """Get API base URL."""
        return BYBIT_API_TESTNET if self.use_testnet else BYBIT_API_MAINNET

    async def start(self) -> None:
        """Start the executor."""
        self._session = aiohttp.ClientSession()
        logger.info(f"Bybit executor started (testnet={self.use_testnet})")

    async def stop(self) -> None:
        """Stop the executor."""
        # Cancel any pending limit order tasks
        for task in self._pending_limit_orders.values():
            task.cancel()
        self._pending_limit_orders.clear()

        if self._session:
            await self._session.close()
            self._session = None

    async def enter_position(
        self,
        side: PositionSide,
        size: float,
        limit_price: Optional[float] = None,
        quote: Optional[Quote] = None,
    ) -> ExecutionResult:
        """Enter a position.

        If use_limit_for_entry is True and limit_price is provided,
        places a limit order with timeout. Falls back to market on timeout.

        Args:
            side: LONG or SHORT
            size: Position size in base currency
            limit_price: Limit price (optional)
            quote: Current quote for market order pricing

        Returns:
            ExecutionResult
        """
        order_side = OrderSide.BUY if side == PositionSide.LONG else OrderSide.SELL

        # Determine order type
        if self.execution_config.use_limit_for_entry and limit_price:
            return await self._execute_limit_with_timeout(
                side=order_side,
                qty=size,
                price=limit_price,
                reduce_only=False,
            )
        else:
            # Market order
            return await self._execute_market(
                side=order_side,
                qty=size,
                reduce_only=False,
            )

    async def exit_position(
        self,
        side: PositionSide,
        size: float,
    ) -> ExecutionResult:
        """Exit a position with market order.

        Args:
            side: Current position side (we exit opposite)
            size: Size to close

        Returns:
            ExecutionResult
        """
        # Exit is opposite side
        order_side = OrderSide.SELL if side == PositionSide.LONG else OrderSide.BUY

        return await self._execute_market(
            side=order_side,
            qty=size,
            reduce_only=True,
        )

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order.

        Args:
            order_id: Order ID to cancel

        Returns:
            True if cancelled successfully
        """
        try:
            params = {
                "category": "linear",
                "symbol": self.symbol,
                "orderId": order_id,
            }

            result = await self._signed_request("POST", "/v5/order/cancel", params)

            if result.get("retCode") == 0:
                if order_id in self._orders:
                    self._orders[order_id].status = OrderStatus.CANCELLED
                return True
            else:
                logger.warning(f"Cancel failed: {result.get('retMsg')}")
                return False

        except Exception as e:
            logger.error(f"Cancel order error: {e}")
            return False

    async def get_position(self) -> Optional[Dict[str, Any]]:
        """Get current position for symbol.

        Returns:
            Position dict or None
        """
        try:
            params = {
                "category": "linear",
                "symbol": self.symbol,
            }

            result = await self._signed_request("GET", "/v5/position/list", params)

            if result.get("retCode") == 0:
                positions = result.get("result", {}).get("list", [])
                if positions:
                    return positions[0]
            return None

        except Exception as e:
            logger.error(f"Get position error: {e}")
            return None

    async def _execute_market(
        self,
        side: OrderSide,
        qty: float,
        reduce_only: bool,
    ) -> ExecutionResult:
        """Execute a market order."""
        client_order_id = self._generate_client_order_id()

        params = {
            "category": "linear",
            "symbol": self.symbol,
            "side": side.value,
            "orderType": OrderType.MARKET.value,
            "qty": str(qty),
            "reduceOnly": reduce_only,
            "orderLinkId": client_order_id,
        }

        try:
            result = await self._signed_request("POST", "/v5/order/create", params)

            if result.get("retCode") == 0:
                order_result = result.get("result", {})
                order_id = order_result.get("orderId", "")

                order = Order(
                    order_id=order_id,
                    client_order_id=client_order_id,
                    symbol=self.symbol,
                    side=side,
                    order_type=OrderType.MARKET,
                    price=None,
                    qty=qty,
                    status=OrderStatus.FILLED,
                    filled_qty=qty,
                )
                self._orders[order_id] = order

                # Get fill price from order details
                fill_price = await self._get_fill_price(order_id)

                return ExecutionResult(
                    success=True,
                    order=order,
                    filled_price=fill_price,
                    filled_qty=qty,
                )
            else:
                return ExecutionResult(
                    success=False,
                    error=result.get("retMsg", "Unknown error"),
                )

        except Exception as e:
            return ExecutionResult(success=False, error=str(e))

    async def _execute_limit_with_timeout(
        self,
        side: OrderSide,
        qty: float,
        price: float,
        reduce_only: bool,
    ) -> ExecutionResult:
        """Execute limit order with timeout, fallback to market."""
        client_order_id = self._generate_client_order_id()

        # Round price to tick size
        price = self._round_to_tick(price)

        params = {
            "category": "linear",
            "symbol": self.symbol,
            "side": side.value,
            "orderType": OrderType.LIMIT.value,
            "qty": str(qty),
            "price": str(price),
            "reduceOnly": reduce_only,
            "orderLinkId": client_order_id,
            "timeInForce": "GTC",
        }

        try:
            result = await self._signed_request("POST", "/v5/order/create", params)

            if result.get("retCode") != 0:
                return ExecutionResult(
                    success=False,
                    error=result.get("retMsg", "Order creation failed"),
                )

            order_result = result.get("result", {})
            order_id = order_result.get("orderId", "")

            order = Order(
                order_id=order_id,
                client_order_id=client_order_id,
                symbol=self.symbol,
                side=side,
                order_type=OrderType.LIMIT,
                price=price,
                qty=qty,
                status=OrderStatus.NEW,
                created_at=int(time.time() * 1000),
            )
            self._orders[order_id] = order

            # Wait for fill with timeout
            timeout_sec = self.execution_config.limit_order_timeout_minutes * 60
            filled = await self._wait_for_fill(order_id, timeout_sec)

            if filled:
                fill_price = await self._get_fill_price(order_id)
                return ExecutionResult(
                    success=True,
                    order=order,
                    filled_price=fill_price,
                    filled_qty=qty,
                )
            else:
                # Timeout - cancel and go market
                logger.info(f"Limit order timeout, converting to market")
                await self.cancel_order(order_id)

                # Execute market order
                return await self._execute_market(side, qty, reduce_only)

        except Exception as e:
            return ExecutionResult(success=False, error=str(e))

    async def _wait_for_fill(self, order_id: str, timeout_sec: float) -> bool:
        """Wait for order to fill.

        Args:
            order_id: Order ID to monitor
            timeout_sec: Timeout in seconds

        Returns:
            True if filled, False if timeout
        """
        start = time.time()
        poll_interval = 0.5

        while time.time() - start < timeout_sec:
            try:
                params = {
                    "category": "linear",
                    "symbol": self.symbol,
                    "orderId": order_id,
                }
                result = await self._signed_request("GET", "/v5/order/realtime", params)

                if result.get("retCode") == 0:
                    orders = result.get("result", {}).get("list", [])
                    if orders:
                        order_data = orders[0]
                        status = order_data.get("orderStatus")

                        if status == "Filled":
                            if order_id in self._orders:
                                self._orders[order_id].status = OrderStatus.FILLED
                            return True
                        elif status in ("Cancelled", "Rejected"):
                            return False

            except Exception as e:
                logger.warning(f"Fill check error: {e}")

            await asyncio.sleep(poll_interval)

        return False

    async def _get_fill_price(self, order_id: str) -> Optional[float]:
        """Get average fill price for an order."""
        try:
            params = {
                "category": "linear",
                "symbol": self.symbol,
                "orderId": order_id,
            }
            result = await self._signed_request("GET", "/v5/order/history", params)

            if result.get("retCode") == 0:
                orders = result.get("result", {}).get("list", [])
                if orders:
                    return float(orders[0].get("avgPrice", 0))
            return None

        except Exception as e:
            logger.warning(f"Get fill price error: {e}")
            return None

    async def _signed_request(
        self,
        method: str,
        endpoint: str,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Make a signed API request."""
        if not self._session:
            raise RuntimeError("Executor not started")

        timestamp = str(int(time.time() * 1000))
        recv_window = "5000"

        # Create signature
        if method == "GET":
            query_string = "&".join(f"{k}={v}" for k, v in params.items())
            sign_payload = f"{timestamp}{self.api_key}{recv_window}{query_string}"
            url = f"{self.base_url}{endpoint}?{query_string}"
            body = None
        else:
            import json
            body = json.dumps(params)
            sign_payload = f"{timestamp}{self.api_key}{recv_window}{body}"
            url = f"{self.base_url}{endpoint}"

        signature = hmac.new(
            self.api_secret.encode(),
            sign_payload.encode(),
            hashlib.sha256
        ).hexdigest()

        headers = {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-SIGN": signature,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": recv_window,
            "Content-Type": "application/json",
        }

        if method == "GET":
            async with self._session.get(url, headers=headers) as resp:
                return await resp.json()
        else:
            async with self._session.post(url, headers=headers, data=body) as resp:
                return await resp.json()

    def _generate_client_order_id(self) -> str:
        """Generate unique client order ID."""
        return f"at_{uuid.uuid4().hex[:16]}"

    def _round_to_tick(self, price: float) -> float:
        """Round price to tick size."""
        return round(price / self.tick_size) * self.tick_size


class PaperExecutor:
    """Paper trading executor for simulation.

    Simulates order execution without actual API calls.
    """

    def __init__(self, config: Config):
        self.config = config
        self.execution_config = config.execution
        self.tick_size = config.instrument.tick_size

        self._orders: List[Order] = []
        self._next_order_id = 1

    async def start(self) -> None:
        """Start the executor."""
        logger.info("Paper executor started")

    async def stop(self) -> None:
        """Stop the executor."""
        pass

    async def enter_position(
        self,
        side: PositionSide,
        size: float,
        limit_price: Optional[float] = None,
        quote: Optional[Quote] = None,
    ) -> ExecutionResult:
        """Simulate position entry."""
        order_side = OrderSide.BUY if side == PositionSide.LONG else OrderSide.SELL

        # Simulate fill at quote price with slippage
        if quote:
            if order_side == OrderSide.BUY:
                fill_price = quote.ask_px + self.tick_size * self.execution_config.slippage_ticks_entry
            else:
                fill_price = quote.bid_px - self.tick_size * self.execution_config.slippage_ticks_entry
        elif limit_price:
            fill_price = limit_price
        else:
            return ExecutionResult(success=False, error="No price available")

        order = Order(
            order_id=str(self._next_order_id),
            client_order_id=f"paper_{self._next_order_id}",
            symbol=self.config.instrument.symbol,
            side=order_side,
            order_type=OrderType.MARKET,
            price=fill_price,
            qty=size,
            status=OrderStatus.FILLED,
            filled_qty=size,
            avg_price=fill_price,
        )
        self._next_order_id += 1
        self._orders.append(order)

        return ExecutionResult(
            success=True,
            order=order,
            filled_price=fill_price,
            filled_qty=size,
        )

    async def exit_position(
        self,
        side: PositionSide,
        size: float,
        quote: Optional[Quote] = None,
    ) -> ExecutionResult:
        """Simulate position exit."""
        order_side = OrderSide.SELL if side == PositionSide.LONG else OrderSide.BUY

        # Simulate fill at quote price with slippage
        if quote:
            if order_side == OrderSide.SELL:
                fill_price = quote.bid_px - self.tick_size * self.execution_config.slippage_ticks_exit
            else:
                fill_price = quote.ask_px + self.tick_size * self.execution_config.slippage_ticks_exit
        else:
            return ExecutionResult(success=False, error="No quote available")

        order = Order(
            order_id=str(self._next_order_id),
            client_order_id=f"paper_{self._next_order_id}",
            symbol=self.config.instrument.symbol,
            side=order_side,
            order_type=OrderType.MARKET,
            price=fill_price,
            qty=size,
            status=OrderStatus.FILLED,
            filled_qty=size,
            avg_price=fill_price,
        )
        self._next_order_id += 1
        self._orders.append(order)

        return ExecutionResult(
            success=True,
            order=order,
            filled_price=fill_price,
            filled_qty=size,
        )

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel order (always succeeds in paper mode)."""
        return True

    async def get_position(self) -> Optional[Dict[str, Any]]:
        """Get position (not tracked in paper mode)."""
        return None
