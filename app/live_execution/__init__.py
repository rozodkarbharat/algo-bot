"""
Live execution package.

Modules:
  order_state_machine  — order lifecycle transitions + audit log
  live_position_manager — in-memory book, MTM, SL, EOD, reconciliation
  live_risk_manager     — pre-trade risk gate
  failsafe              — kill switch, market hours, duplicate prevention
  execution_engine      — signal → risk → broker order pipeline
"""
