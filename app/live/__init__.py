"""
Live intraday signal engine.

Subpackage for the real-time market processing pipeline:
  candle_builder  — aggregate ticks into 1m / 5m / 15m candles
  market_session  — open/close detection, daily reset, entry-window control
  signal_engine   — ORB breakout detection, signal generation, dedup
  market_engine   — subscribes shortlisted stocks, fans ticks into builders,
                    triggers the signal engine on candle close

Design rules (mirror PROJECT_CONTEXT.md §17.4):
  - Live engine is broker-independent — it receives ticks via a callback and
    has no Angel One imports.
  - Live engine emits LiveSignal objects only; it never places orders.
  - All persistence is mediated through repositories.
  - Live engine modules import each other but NOT services / routes.
"""
