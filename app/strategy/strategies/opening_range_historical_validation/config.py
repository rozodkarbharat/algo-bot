"""
Opening Range Historical Validation — strategy configuration dataclass.

All fields are JSON-serialisable so the config dict can be stored in
BacktestRun.configuration for full reproducibility.
"""

from __future__ import annotations

from dataclasses import dataclass, fields as dc_fields

from app.strategy.strategies.opening_range_historical_validation.constants import (
    DEFAULT_BROKERAGE_PER_SIDE,
    DEFAULT_CAPITAL_PER_TRADE,
    DEFAULT_LOOKBACK_OCCURRENCES,
    DEFAULT_MAX_ORB_RANGE_PCT,
    DEFAULT_SLIPPAGE_PCT,
    MIN_OCCURRENCES_REQUIRED,
    QUALIFICATION_MIN_WIN_RATE,
    QUALIFICATION_MIN_WINS,
)


@dataclass
class ORHVConfig:
    """
    Complete tunable configuration for the Opening Range Historical Validation strategy.

    Phases:
        Phase 1 — Setup Detection: no tunable parameters (pattern is fixed)
        Phase 2 — Historical Validation: lookback_occurrences, qualification thresholds
        Phase 3 — Next-Day Execution: max_orb_range_pct, max_entry_time_ist
    """

    # ── Phase 2 ───────────────────────────────────────────────────────────────
    lookback_occurrences: int = DEFAULT_LOOKBACK_OCCURRENCES
    """Number of prior historical setups to validate against (max)."""

    min_occurrences_required: int = MIN_OCCURRENCES_REQUIRED
    """Minimum prior setups before a trade is considered statistically valid."""

    qualification_min_wins: int = QUALIFICATION_MIN_WINS
    """Absolute win count threshold when lookback_occurrences worth of data is available."""

    qualification_min_win_rate: float = QUALIFICATION_MIN_WIN_RATE
    """Win-rate threshold (0.0–1.0) — either criterion (wins OR rate) qualifies."""

    history_coverage_min_fraction: float = 0.8
    """
    Fraction of the active universe that must already have a stored setup on a
    historical day before the backfill guard treats that day as 'already
    detected'. Below this, the day is re-detected so partial history (e.g. a few
    single-symbol test runs) cannot silently block a full-universe backfill.
    """

    # ── Phase 3 ───────────────────────────────────────────────────────────────
    max_orb_range_pct: float = DEFAULT_MAX_ORB_RANGE_PCT
    """D+1 first-candle range must be ≤ this % of OR_Close.  Wider ORBs are skipped."""

    max_entry_time_ist: str = "12:00"
    """Latest candle open time (HH:MM IST) after which no new entry is accepted."""

    # ── Capital & costs ───────────────────────────────────────────────────────
    capital_per_trade: float = DEFAULT_CAPITAL_PER_TRADE
    """Capital per simulated trade in ₹."""

    slippage_pct: float = DEFAULT_SLIPPAGE_PCT
    """Slippage % applied to both entry and exit fills."""

    brokerage_per_side: float = DEFAULT_BROKERAGE_PER_SIDE
    """Flat brokerage in ₹ per trade side (entry or exit)."""

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in dc_fields(self)}

    @classmethod
    def from_dict(cls, d: dict) -> "ORHVConfig":
        """Create from a plain dict; unknown keys are silently ignored."""
        field_names = {f.name for f in dc_fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in field_names})
