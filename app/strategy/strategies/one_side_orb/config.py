"""
One-Side ORB strategy configuration dataclass.

Wraps all tunable parameters for the One-Side ORB strategy as a single,
validated object.  Serialises to/from plain dict for DB storage and API
transport.

Design:
  - Fields mirror BacktestConfig exactly so config dicts are interchangeable.
  - from_dict() is permissive: unknown keys are ignored (forward-compat).
  - to_dict() output is used as BacktestRun.configuration and in API responses.
"""

from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass
class OneSideORBConfig:
    """
    Complete tunable configuration for the One-Side ORB strategy.

    Defaults match the system-wide settings but can be overridden per backtest
    or research run without touching global settings.
    """

    # ── OSD detection ─────────────────────────────────────────────────────────
    min_move_percent: float = 1.0
    """Minimum % move from ORB boundary for a day to qualify as one-side."""

    # ── Candidate filtering ───────────────────────────────────────────────────
    probability_threshold: float = 0.70
    """Minimum historical continuation probability (0.0–1.0) to include a stock."""

    max_orb_range_pct: float = 1.0
    """Maximum first-candle range % (wider = higher risk; skip if exceeded)."""

    max_entry_time_ist: str = "11:30"
    """Latest allowed entry time in IST (HH:MM).  After this, no new entry."""

    # ── Probability engine ────────────────────────────────────────────────────
    lookback_days: int = 252
    """Trading-day lookback for continuation probability.  252 ≈ 1 year."""

    min_occurrences: int = 10
    """Minimum OSD event count before continuation stats are considered reliable."""

    # ── Capital & costs ───────────────────────────────────────────────────────
    capital_per_trade: float = 100_000.0
    """Capital allocated per trade in ₹."""

    slippage_pct: float = 0.05
    """Expected slippage % applied to both entry and exit fills."""

    brokerage_per_side: float = 20.0
    """Flat brokerage cost in ₹ per trade side (entry or exit)."""

    sl_buffer_pct: float = 0.0
    """Extra % buffer beyond the ORB boundary for stop-loss placement."""

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, d: dict) -> "OneSideORBConfig":
        """Create from a plain dict.  Unknown keys are silently ignored."""
        field_names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in field_names})
