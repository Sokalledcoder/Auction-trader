"""FastAPI backend for the auction-trader dashboard.

Serves:
- Static dashboard files
- WebSocket for real-time updates
- REST endpoints for historical data
"""

import asyncio
import logging
import random
import statistics
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Dashboard directory
DASHBOARD_DIR = Path(__file__).parent
STATIC_DIR = DASHBOARD_DIR / "static"

# Create FastAPI app
app = FastAPI(
    title="Auction Trader Dashboard",
    description="Real-time trading dashboard for AMT-based BTC trading",
    version="0.1.0",
)

# CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ============================================================================
# Models
# ============================================================================

class ValueArea(BaseModel):
    poc: float
    vah: float
    val: float
    coverage: float
    is_valid: bool


class OrderFlow(BaseModel):
    of_1m: float
    of_norm_1m: float
    buy_volume: float
    sell_volume: float


class Position(BaseModel):
    side: Optional[str] = None
    entry_price: Optional[float] = None
    size: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    stop_price: Optional[float] = None
    tp1_price: Optional[float] = None
    tp2_price: Optional[float] = None


class Signal(BaseModel):
    ts: int
    signal_type: str
    action: str
    price: float
    reason: str


class Stats(BaseModel):
    total_trades: int
    win_rate: float
    total_pnl: float
    max_drawdown: float
    avg_hold_minutes: float


class DashboardState(BaseModel):
    timestamp: int
    price: float
    va: ValueArea
    order_flow: OrderFlow
    position: Position
    recent_signals: List[Signal]
    stats: Stats
    price_history: List[Dict[str, float]]


# ============================================================================
# WebSocket Manager
# ============================================================================

class ConnectionManager:
    """Manage WebSocket connections."""

    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"Client connected. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
        logger.info(f"Client disconnected. Total: {len(self.active_connections)}")

    async def broadcast(self, data: dict):
        """Send data to all connected clients."""
        for connection in self.active_connections:
            try:
                await connection.send_json(data)
            except Exception as e:
                logger.warning(f"Failed to send to client: {e}")


manager = ConnectionManager()


# ============================================================================
# Mock Data Generator (Replace with real data in production)
# ============================================================================

class MockDataGenerator:
    """Generate mock trading data for dashboard testing."""

    def __init__(self):
        self.base_price = 97500.0
        self.price_history: List[Dict[str, float]] = []
        self.signals: List[Signal] = []
        self.position: Optional[Dict] = None
        self.trades_count = 0
        self.wins = 0
        self.total_pnl = 0.0
        self._init_history()

    def _init_history(self):
        """Initialize price history."""
        ts = int(datetime.now().timestamp() * 1000) - 240 * 60 * 1000
        price = self.base_price

        for i in range(240):
            price += random.gauss(0, 15)
            self.price_history.append({
                "ts": ts + i * 60_000,
                "price": price,
                "volume": random.uniform(10, 100),
            })

        self.base_price = price

    def _calculate_stats(self) -> Stats:
        """Calculate current trading statistics."""
        win_rate = self.wins / self.trades_count if self.trades_count > 0 else 0
        max_dd = abs(min(0, self.total_pnl)) / 10000
        return Stats(
            total_trades=self.trades_count,
            win_rate=win_rate,
            total_pnl=self.total_pnl,
            max_drawdown=max_dd,
            avg_hold_minutes=random.uniform(5, 30),
        )

    def generate_tick(self) -> DashboardState:
        """Generate a new tick of data."""
        ts = int(datetime.now().timestamp() * 1000)

        # Update price
        self.base_price += random.gauss(0, 8)
        price = self.base_price

        # Update history
        self.price_history.append({
            "ts": ts,
            "price": price,
            "volume": random.uniform(10, 100),
        })
        if len(self.price_history) > 300:
            self.price_history = self.price_history[-300:]

        # Calculate VA from recent prices
        recent = [p["price"] for p in self.price_history[-240:]]
        poc = statistics.mean(recent)
        std = statistics.stdev(recent) if len(recent) > 1 else 0
        va = ValueArea(
            poc=poc,
            vah=poc + std * 0.67,
            val=poc - std * 0.67,
            coverage=0.70,
            is_valid=True,
        )

        # Order flow
        of_raw = random.gauss(0, 50)
        order_flow = OrderFlow(
            of_1m=of_raw,
            of_norm_1m=of_raw / 100,
            buy_volume=random.uniform(20, 80),
            sell_volume=random.uniform(20, 80),
        )

        # Random signal generation (5% chance)
        if random.random() < 0.05 and not self.position:
            signal_type = random.choice([
                "BREAKIN_LONG", "BREAKIN_SHORT",
                "BREAKOUT_LONG", "BREAKOUT_SHORT",
                "FAILED_BREAKOUT_LONG", "FAILED_BREAKOUT_SHORT"
            ])
            is_long = "LONG" in signal_type
            signal = Signal(
                ts=ts,
                signal_type=signal_type,
                action="ENTER_LONG" if is_long else "ENTER_SHORT",
                price=price,
                reason=f"{signal_type.replace('_', ' ').title()} detected",
            )
            self.signals.append(signal)
            if len(self.signals) > 20:
                self.signals = self.signals[-20:]

            # Open position
            self.position = {
                "side": "LONG" if is_long else "SHORT",
                "entry_price": price,
                "size": 0.1,
                "stop_price": price - 100 if is_long else price + 100,
                "tp1_price": va.poc,
                "tp2_price": va.vah if is_long else va.val,
            }

        # Close position randomly
        if self.position and random.random() < 0.03:
            entry = self.position["entry_price"]
            pnl = (price - entry) if self.position["side"] == "LONG" else (entry - price)
            pnl *= self.position["size"]
            self.total_pnl += pnl
            self.trades_count += 1
            if pnl > 0:
                self.wins += 1
            self.position = None

        # Position with unrealized PnL
        position = Position()
        if self.position:
            entry = self.position["entry_price"]
            unrealized = (price - entry) if self.position["side"] == "LONG" else (entry - price)
            unrealized *= self.position["size"]
            position = Position(
                side=self.position["side"],
                entry_price=entry,
                size=self.position["size"],
                unrealized_pnl=unrealized,
                stop_price=self.position["stop_price"],
                tp1_price=self.position["tp1_price"],
                tp2_price=self.position["tp2_price"],
            )

        return DashboardState(
            timestamp=ts,
            price=price,
            va=va,
            order_flow=order_flow,
            position=position,
            recent_signals=self.signals[-10:],
            stats=self._calculate_stats(),
            price_history=self.price_history[-120:],
        )


mock_generator = MockDataGenerator()


# ============================================================================
# Routes
# ============================================================================

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve the main dashboard."""
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return HTMLResponse("<h1>Dashboard not found</h1>", status_code=404)


@app.get("/api/state")
async def get_state():
    """Get current dashboard state."""
    return mock_generator.generate_tick()


@app.get("/api/history")
async def get_history(minutes: int = 240):
    """Get price history."""
    return {"history": mock_generator.price_history[-minutes:]}


@app.get("/api/signals")
async def get_signals(limit: int = 50):
    """Get recent signals."""
    return {"signals": mock_generator.signals[-limit:]}


@app.get("/api/stats")
async def get_stats():
    """Get trading statistics."""
    return mock_generator._calculate_stats()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates."""
    await manager.connect(websocket)
    try:
        while True:
            # Send updates every second
            state = mock_generator.generate_tick()
            await websocket.send_json(state.model_dump())
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)


# Mount static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ============================================================================
# Main
# ============================================================================

def run_dashboard(host: str = "127.0.0.1", port: int = 8080):
    """Run the dashboard server."""
    import uvicorn
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_dashboard()
