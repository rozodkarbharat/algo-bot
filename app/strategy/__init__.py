"""
Strategy package — multi-strategy trading framework.

Architecture:
  base_strategy.py     — abstract BaseStrategy contract all strategies implement
  strategy_registry.py — singleton registry (strategy_id → BaseStrategy instance)
  strategies/          — concrete strategy implementations (one per sub-package)
  templates/           — copy-paste skeleton for new strategies

Active strategies (registered at import time):
  strategies/one_side_orb/  — One-Side Opening Range Breakout

Pure engine modules (unchanged, imported directly by services):
  one_side_detector.py        — OSD detection logic
  continuation_probability.py — continuation probability engine
  backtest_engine.py          — historical replay engine
  trade_simulator.py          — per-trade simulation
  metrics_engine.py           — performance metrics calculation

Adding a new strategy:
  1. Copy templates/new_strategy_template/ → strategies/your_id/
  2. Implement BaseStrategy in strategies/your_id/strategy.py
  3. Register in strategy_registry._initialize_registry()
  4. Write tests in tests/test_strategy_your_id.py
"""

# Initialize the strategy registry — must happen before any route/service
# imports strategy objects so all strategies are available at startup.
from app.strategy.strategy_registry import _initialize_registry  # noqa: F401

_initialize_registry()
