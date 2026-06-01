"""
New Strategy Template — configuration dataclass.

INSTRUCTIONS:
  1. Rename class to YourStrategyNameConfig.
  2. Replace / add fields for all tunable parameters.
  3. Implement validate() with range checks for all numeric fields.
  4. Keep to_dict() / from_dict() in sync with your fields.

Constraints:
  - All fields must be JSON-serialisable (str, int, float, bool, list, dict).
  - Use clear names that match the parameter keys in BaseStrategy.get_metadata().
"""

from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass
class MyStrategyConfig:            # ← RENAME THIS
    """Tunable configuration for MyStrategy."""

    # ── Example parameters — replace with your own ────────────────────────────
    probability_threshold: float = 0.65
    """Minimum probability to include a stock in today's shortlist."""

    capital_per_trade: float = 100_000.0
    """Capital per trade in ₹."""

    slippage_pct: float = 0.05
    brokerage_per_side: float = 20.0

    # Add more strategy-specific parameters here ...

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, d: dict) -> "MyStrategyConfig":
        field_names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in field_names})
