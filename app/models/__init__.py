"""
Models package — Beanie ODM document definitions.

Each file in this package defines one MongoDB collection.
Register every Document subclass in app/database/init_db.py.

Planned models:
  candle.py     — OHLCV candlestick data
  signal.py     — strategy-generated trade signals
  order.py      — order records (both paper and live)
  strategy.py   — strategy configuration and state
  instrument.py — tradeable instrument master data
"""
