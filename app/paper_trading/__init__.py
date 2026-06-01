"""
Paper trading subsystem.

Consumes live signals from the Live Signal Engine and simulates realistic
order execution without touching any broker. This package contains pure,
broker-independent components:

  - paper_execution_engine.py — fill simulation (slippage + brokerage)
  - position_manager.py        — open positions, LTP updates, SL + EOD exits
  - risk_manager.py            — pre-trade risk checks (loss/cooldown/limits)
  - pnl_engine.py              — realized / unrealized / equity-curve maths
  - session_manager.py         — daily reset, EOD close, archival

The orchestrator service `PaperTradingService` lives in app/services/ and
is the only layer that touches repositories and WebSocket broadcasts.
"""
