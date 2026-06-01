"""
Walk-Forward Window Generator
------------------------------
Produces non-overlapping train/test window pairs over a date range.

Each fold:
  - training window  : training_months calendar months
  - testing  window  : testing_months  calendar months
  - advance by       : step_months     calendar months after each fold

No external I/O, no DB — pure Python 3.12.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from typing import Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Calendar arithmetic helper
# ---------------------------------------------------------------------------

def _add_months(d: date, months: int) -> date:
    """Add *months* calendar months to *d*, clamping to the last day of the
    target month when necessary (e.g. Jan 31 + 1 month → Feb 28/29)."""
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WalkForwardWindow:
    segment_number: int       # 1-based
    training_start: date
    training_end: date        # inclusive
    testing_start: date       # one day after training_end
    testing_end: date         # inclusive


@dataclass
class WalkForwardConfig:
    from_date: date           # overall start (training_start of first window)
    to_date: date             # overall end   (testing_end  of last  window)
    training_months: int = 12 # length of each training window in calendar months
    testing_months: int = 3   # length of each testing window  in calendar months
    step_months: int = 3      # months to advance the entire window each fold
    strategy_id: str = "one_side_orb"
    strategy_name: str = "One-Side ORB"
    symbols: Optional[list[str]] = field(default=None)
    # Base strategy params for optimisation baseline
    base_probability_threshold: float = 0.70
    base_max_orb_range_pct: float = 1.00
    base_max_entry_time_ist: str = "11:30"
    base_sl_buffer_pct: float = 0.00
    capital_per_trade: float = 100_000.0
    slippage_pct: float = 0.05
    brokerage_per_side: float = 20.0

    def to_dict(self) -> dict:
        """Return all fields as a JSON-serialisable dict (dates → ISO strings)."""
        raw = asdict(self)
        raw["from_date"] = self.from_date.isoformat()
        raw["to_date"] = self.to_date.isoformat()
        return raw


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

class WalkForwardWindowGenerator:
    """Generate walk-forward train/test windows from a WalkForwardConfig."""

    def __init__(self, config: WalkForwardConfig) -> None:
        self.config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(self) -> None:
        """Raise ValueError if the config is logically invalid."""
        cfg = self.config

        if cfg.training_months <= 0:
            raise ValueError(
                f"training_months must be > 0, got {cfg.training_months}"
            )
        if cfg.testing_months <= 0:
            raise ValueError(
                f"testing_months must be > 0, got {cfg.testing_months}"
            )
        if cfg.step_months <= 0:
            raise ValueError(
                f"step_months must be > 0, got {cfg.step_months}"
            )
        if cfg.from_date >= cfg.to_date:
            raise ValueError(
                f"from_date ({cfg.from_date}) must be strictly before "
                f"to_date ({cfg.to_date})"
            )
        if cfg.capital_per_trade <= 0:
            raise ValueError(
                f"capital_per_trade must be > 0, got {cfg.capital_per_trade}"
            )
        if not (0.0 < cfg.base_probability_threshold <= 1.0):
            raise ValueError(
                f"base_probability_threshold must be in (0, 1], "
                f"got {cfg.base_probability_threshold}"
            )

    def generate(self) -> list[WalkForwardWindow]:
        """Build and return the list of walk-forward windows.

        Algorithm
        ---------
        1. training_start = config.from_date
        2. training_end   = training_start + training_months - 1 day
        3. testing_start  = training_end   + 1 day
        4. testing_end    = testing_start  + testing_months  - 1 day
        5. If testing_end > config.to_date → use config.to_date as testing_end
           (partial last window), then stop after appending.
        6. Advance training_start by step_months and repeat from step 2.

        Raises
        ------
        ValueError
            If the date range is too short to produce even one complete window.
        """
        self.validate()

        cfg = self.config
        windows: list[WalkForwardWindow] = []
        segment_number = 1
        training_start = cfg.from_date

        logger.info(
            "WalkForwardWindowGenerator starting. "
            "range=[%s, %s]  training_months=%d  testing_months=%d  step_months=%d",
            cfg.from_date, cfg.to_date,
            cfg.training_months, cfg.testing_months, cfg.step_months,
        )

        while True:
            training_end = _add_months(training_start, cfg.training_months) - timedelta(days=1)
            testing_start = training_end + timedelta(days=1)
            testing_end = _add_months(testing_start, cfg.testing_months) - timedelta(days=1)

            # The training window itself must fit within the overall range.
            if training_end >= cfg.to_date:
                logger.info(
                    "Stopping: training_end (%s) >= to_date (%s) — "
                    "no room for a testing window.",
                    training_end, cfg.to_date,
                )
                break

            is_partial = testing_end > cfg.to_date
            if is_partial:
                testing_end = cfg.to_date

            window = WalkForwardWindow(
                segment_number=segment_number,
                training_start=training_start,
                training_end=training_end,
                testing_start=testing_start,
                testing_end=testing_end,
            )
            windows.append(window)

            logger.info(
                "Window #%d generated | train=[%s, %s] (%d months) | "
                "test=[%s, %s] (%s)",
                segment_number,
                training_start, training_end, cfg.training_months,
                testing_start, testing_end,
                "partial — clamped to to_date" if is_partial else f"{cfg.testing_months} months",
            )

            if is_partial:
                logger.info(
                    "Partial window reached to_date (%s). Generation complete.",
                    cfg.to_date,
                )
                break

            # Advance the window by step_months and continue.
            training_start = _add_months(training_start, cfg.step_months)
            segment_number += 1

        if not windows:
            raise ValueError(
                f"Date range [{cfg.from_date}, {cfg.to_date}] is too short to "
                f"produce even one walk-forward window "
                f"(need at least {cfg.training_months + cfg.testing_months} months)."
            )

        logger.info(
            "WalkForwardWindowGenerator complete: %d window(s) generated.",
            len(windows),
        )
        return windows
