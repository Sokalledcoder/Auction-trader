"""Auction Trader Dashboard.

A real-time trading dashboard with WebSocket updates.
"""

from .api import app, run_dashboard

__all__ = ["app", "run_dashboard"]
