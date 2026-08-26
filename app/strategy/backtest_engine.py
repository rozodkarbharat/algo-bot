"""
Shared backtest types used by all strategies.

Strategy-specific replay engines live under
`app/strategy/strategies/<id>/` and are created via
`BaseStrategy.create_backtest_engine()`.

This module only holds:
  - BacktestConfig — serialisable run parameters
  - BacktestEngineResult — common engine output shape
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from app.models.historical_candle import CandleData
from app.strategy.trade_simulator import SimulatedTrade


@dataclass
class BacktestConfig:
    """
    Complete configuration for a historical backtest run.

    All parameters are serialisable to dict so they can be stored in the
    BacktestRun.configuration field for reproducibility.
    Strategy-specific engines ignore unknown keys via their own from_dict().
    """

    # Date range
    from_date: date
    to_date: date

    # Symbol scope — None = use whatever symbols are passed to the engine
    symbols: Optional[list[str]] = None

    # Strategy / filter parameters (ORHV uses max_orb_range + entry cutoff)
    probability_threshold: float = 0.70
    min_move_percent: float = 1.0
    max_orb_range_pct: float = 1.0

    # Entry window — candle OPEN time must be ≤ this (IST HH:MM string)
    max_entry_time_ist: str = "12:00"

    # Capital / cost
    capital_per_trade: float = 100_000.0
    slippage_pct: float = 0.05
    brokerage_per_side: float = 20.0
    sl_buffer_pct: float = 0.0

    def to_dict(self) -> dict:
        return {
            "from_date": self.from_date.isoformat(),
            "to_date": self.to_date.isoformat(),
            "symbols": self.symbols,
            "probability_threshold": self.probability_threshold,
            "min_move_percent": self.min_move_percent,
            "max_orb_range_pct": self.max_orb_range_pct,
            "max_entry_time_ist": self.max_entry_time_ist,
            "capital_per_trade": self.capital_per_trade,
            "slippage_pct": self.slippage_pct,
            "brokerage_per_side": self.brokerage_per_side,
            "sl_buffer_pct": self.sl_buffer_pct,
        }


@dataclass
class BacktestEngineResult:
    """Aggregate output returned by strategy backtest engines."""

    trades: list[SimulatedTrade] = field(default_factory=list)
    total_candidate_days: int = 0
    total_no_data_days: int = 0
    symbols_processed: list[str] = field(default_factory=list)
    trading_days_processed: int = 0


# Type aliases for pre-fetched data structures passed to engine.run()
# Legacy name kept for call-site compatibility; ORHV ignores this payload.
OsdHistory = dict[str, dict[str, Optional[dict]]]

CandleHistory = dict[str, dict[str, list[CandleData]]]
# candle_history[symbol]["YYYY-MM-DD"] = [CandleData, ...]
