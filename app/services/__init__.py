"""
Services package — business logic layer.

Services orchestrate repositories, brokers, and external APIs.
They contain no HTTP/WebSocket concerns (those stay in routes).

Planned services:
  candle_service.py    — candle ingestion, aggregation, gap-filling
  signal_service.py    — signal lifecycle management
  order_service.py     — order placement, tracking, reconciliation
  strategy_service.py  — strategy registration, start/stop
  market_service.py    — live feed subscription, LTP caching
"""
