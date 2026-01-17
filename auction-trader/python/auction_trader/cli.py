"""Command-line interface for auction-trader.

Usage:
    auction-trader run [--mode=MODE] [--config=PATH]
    auction-trader dashboard [--host=HOST] [--port=PORT]
    auction-trader backtest [--start=DATE] [--end=DATE] [--config=PATH]
    auction-trader status
    auction-trader version
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from .config import load_config, Config
from .orchestrator import Orchestrator, TradingMode


def setup_logging(level: str = "INFO", log_file: str = None) -> None:
    """Configure logging."""
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    handlers = [logging.StreamHandler(sys.stdout)]

    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file))

    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format=log_format,
        handlers=handlers,
    )


def cmd_run(args: argparse.Namespace) -> int:
    """Run the trading system."""
    # Load config
    config = load_config(args.config)

    # Get credentials from environment
    api_key = os.environ.get("BYBIT_API_KEY")
    api_secret = os.environ.get("BYBIT_API_SECRET")
    use_testnet = os.environ.get("BYBIT_TESTNET", "true").lower() == "true"

    # Determine mode
    mode = args.mode or os.environ.get("TRADING_MODE", TradingMode.PAPER)

    if mode == TradingMode.LIVE:
        if not api_key or not api_secret:
            print("Error: API credentials required for live trading")
            print("Set BYBIT_API_KEY and BYBIT_API_SECRET environment variables")
            return 1

        # Safety check
        if not args.confirm_live:
            print("WARNING: You are about to start LIVE trading with real money!")
            print("Add --confirm-live flag to proceed")
            return 1

    # Setup logging
    log_level = os.environ.get("LOG_LEVEL", "INFO")
    log_file = os.environ.get("LOG_FILE")
    setup_logging(log_level, log_file)

    logger = logging.getLogger(__name__)
    logger.info(f"Starting auction-trader in {mode} mode")

    # Create and run orchestrator
    orchestrator = Orchestrator(
        config=config,
        mode=mode,
        api_key=api_key,
        api_secret=api_secret,
        use_testnet=use_testnet,
    )

    try:
        asyncio.run(orchestrator.start())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        asyncio.run(orchestrator.stop())

    # Print final stats
    stats = orchestrator.get_stats()
    print("\nFinal Statistics:")
    print(f"  Bars processed: {stats['bars_processed']}")
    print(f"  Signals generated: {stats['signals_generated']}")
    print(f"  Trades executed: {stats['trades_executed']}")

    pos_stats = stats.get('position_stats', {})
    if pos_stats:
        print(f"  Total trades: {pos_stats.get('total_trades', 0)}")
        print(f"  Win rate: {pos_stats.get('win_rate', 0):.1%}")
        print(f"  Total PnL: {pos_stats.get('total_pnl', 0):.2f}")

    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    """Run a backtest."""
    print("Backtest mode not yet fully implemented")
    print("Use the Rust backtest crate directly for now")

    # Load config
    config = load_config(args.config)

    print(f"Would backtest from {args.start} to {args.end}")
    print(f"Config: {config.instrument.symbol} on {config.instrument.exchange}")

    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Show system status."""
    # In a full implementation, this would connect to a running instance
    print("Auction Trader Status")
    print("=" * 40)
    print("No running instance detected")
    print("\nConfiguration:")

    config = load_config(args.config)
    print(f"  Symbol: {config.instrument.symbol}")
    print(f"  Exchange: {config.instrument.exchange}")
    print(f"  Timeframe: {config.instrument.timeframe}")
    print(f"  Risk per trade: {config.sizing.risk_pct:.1%}")
    print(f"  Max leverage: {config.sizing.max_leverage}x")

    return 0


def cmd_version(args: argparse.Namespace) -> int:
    """Show version information."""
    from . import __version__
    print(f"auction-trader version {__version__}")
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    """Run the web dashboard."""
    try:
        import uvicorn
    except ImportError:
        print("Error: Dashboard requires uvicorn. Install with:")
        print("  pip install uvicorn fastapi")
        return 1

    # Import dashboard module
    dashboard_path = Path(__file__).parent.parent.parent / "dashboard"
    sys.path.insert(0, str(dashboard_path.parent))

    try:
        from dashboard.api import app
    except ImportError as e:
        print(f"Error: Could not import dashboard: {e}")
        return 1

    print(f"Starting Auction Trader Dashboard at http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop")

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info",
    )

    return 0


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="auction-trader",
        description="BTC Perpetual Trading System using Auction Market Theory",
    )
    parser.add_argument(
        "--config", "-c",
        help="Path to config file",
        default=None,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # run command
    run_parser = subparsers.add_parser("run", help="Run the trading system")
    run_parser.add_argument(
        "--mode", "-m",
        choices=["paper", "live", "shadow"],
        default=None,
        help="Trading mode (default: paper)",
    )
    run_parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="Confirm live trading (required for live mode)",
    )
    run_parser.set_defaults(func=cmd_run)

    # dashboard command
    dash_parser = subparsers.add_parser("dashboard", help="Run the web dashboard")
    dash_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)",
    )
    dash_parser.add_argument(
        "--port", "-p",
        type=int,
        default=8080,
        help="Port to run on (default: 8080)",
    )
    dash_parser.set_defaults(func=cmd_dashboard)

    # backtest command
    bt_parser = subparsers.add_parser("backtest", help="Run a backtest")
    bt_parser.add_argument(
        "--start", "-s",
        help="Start date (YYYY-MM-DD)",
        default="2024-01-01",
    )
    bt_parser.add_argument(
        "--end", "-e",
        help="End date (YYYY-MM-DD)",
        default="2024-12-31",
    )
    bt_parser.set_defaults(func=cmd_backtest)

    # status command
    status_parser = subparsers.add_parser("status", help="Show system status")
    status_parser.set_defaults(func=cmd_status)

    # version command
    version_parser = subparsers.add_parser("version", help="Show version")
    version_parser.set_defaults(func=cmd_version)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
