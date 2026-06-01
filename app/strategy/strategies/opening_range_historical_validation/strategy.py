"""
OpeningRangeHistoricalValidationStrategy — BaseStrategy implementation.

Registers the ORHV strategy in the global StrategyRegistry so all
framework-aware services (BacktestService, StrategyService, etc.) can
discover and use it without hard-coding ORHV-specific logic.

Three-phase execution model:
  Phase 1 — Setup Detection (Day D):
      ORHVSetupDetector scans Day D candles for the two-sided breakout pattern.
  Phase 2 — Historical Validation (Day D):
      ORHVHistoricalValidator simulates the last 30 occurrences of the setup
      and gates on win_rate ≥ 70% or wins ≥ 21.
  Phase 3 — Next-Day Execution (Day D+1):
      ORHVSignalGenerator / ORHVBacktestEngine trades whichever side of the
      D+1 opening range breaks first before 12:00 IST.
"""

from __future__ import annotations

from typing import Any, Optional

from app.strategy.base_strategy import BaseStrategy, DayClassificationResult, StrategyMetadata
from app.strategy.strategies.opening_range_historical_validation.config import ORHVConfig
from app.strategy.strategies.opening_range_historical_validation.constants import (
    STRATEGY_CATEGORY,
    STRATEGY_DESCRIPTION,
    STRATEGY_ID,
    STRATEGY_NAME,
    STRATEGY_VERSION,
)


class OpeningRangeHistoricalValidationStrategy(BaseStrategy):
    """
    Opening Range Historical Validation strategy.

    Detects stocks showing a two-sided breakout on Day D (both above the
    first high-side break level and below the first low-side break level).
    Validates historically (last 30 occurrences, ≥70% win rate).
    Executes on Day D+1 on whichever side of the opening range breaks first.
    """

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def strategy_id(self) -> str:
        return STRATEGY_ID

    @property
    def strategy_name(self) -> str:
        return STRATEGY_NAME

    @property
    def strategy_version(self) -> str:
        return STRATEGY_VERSION

    # ── Configuration ─────────────────────────────────────────────────────────

    def get_default_config(self) -> dict:
        return ORHVConfig().to_dict()

    def validate_configuration(self, config: dict) -> None:
        checks = {
            "qualification_min_win_rate": (0.0, 1.0),
            "max_orb_range_pct": (0.01, 10.0),
            "slippage_pct": (0.0, 2.0),
        }
        for key, (lo, hi) in checks.items():
            if key in config:
                val = config[key]
                if not (lo <= val <= hi):
                    raise ValueError(f"'{key}' must be in [{lo}, {hi}], got {val}.")

        if "lookback_occurrences" in config:
            v = config["lookback_occurrences"]
            if not (1 <= v <= 500):
                raise ValueError(f"'lookback_occurrences' must be in [1, 500], got {v}.")

        if "qualification_min_wins" in config:
            v = config["qualification_min_wins"]
            if v < 1:
                raise ValueError(f"'qualification_min_wins' must be >= 1, got {v}.")

        if "capital_per_trade" in config and config["capital_per_trade"] <= 0:
            raise ValueError("'capital_per_trade' must be > 0.")

        if "max_entry_time_ist" in config:
            t = str(config["max_entry_time_ist"])
            parts = t.split(":")
            if len(parts) != 2 or not all(p.isdigit() for p in parts):
                raise ValueError(f"'max_entry_time_ist' must be HH:MM, got '{t}'.")

    # ── Engine factories ──────────────────────────────────────────────────────

    def create_day_classifier(self, config: Optional[dict] = None) -> Any:
        """
        Return an ORHVSetupDetector wrapped to emit DayClassificationResult.

        strategy_signal = None for ORHV (direction is NOT pre-determined).
        """
        from app.strategy.strategies.opening_range_historical_validation.detector import (
            ORHVSetupDetector,
        )
        cfg = ORHVConfig.from_dict(config or {})
        return _ORHVClassifierAdapter(ORHVSetupDetector())

    def create_backtest_engine(self, config: dict) -> Any:
        """
        Return an ORHVBacktestEngine for the given config dict.

        NOTE: The candle_history passed to engine.run() should cover an extended
        date range (≥5 years before from_date) so Phase 2 can find 30 occurrences.
        BacktestService._load_candle_history() handles this for ORHV automatically
        by detecting the strategy_id.
        """
        from app.strategy.strategies.opening_range_historical_validation.backtest_logic import (
            ORHVBacktestEngine,
        )
        cfg = ORHVConfig.from_dict(config)
        return ORHVBacktestEngine(cfg)

    # ── Risk calculations ─────────────────────────────────────────────────────

    def calculate_stop_loss(
        self,
        entry_price: float,
        orb_high: float,
        orb_low: float,
        side: str,
        config: Optional[dict] = None,
    ) -> float:
        """
        ORHV stop loss: opposite side of the Day D+1 opening range.
        LONG → stop at ORL  |  SHORT → stop at ORH
        """
        if side.upper() == "LONG":
            return round(orb_low, 4)
        return round(orb_high, 4)

    def calculate_targets(
        self,
        entry_price: float,
        orb_high: float,
        orb_low: float,
        side: str,
        config: Optional[dict] = None,
    ) -> list[float]:
        """ORHV exits at SL or EOD — no fixed targets."""
        return []

    # ── Metadata ──────────────────────────────────────────────────────────────

    def get_metadata(self) -> StrategyMetadata:
        return StrategyMetadata(
            strategy_id=STRATEGY_ID,
            strategy_name=STRATEGY_NAME,
            version=STRATEGY_VERSION,
            description=STRATEGY_DESCRIPTION,
            category=STRATEGY_CATEGORY,
            parameters={
                "lookback_occurrences": {
                    "type": "int",
                    "range": [1, 500],
                    "default": 30,
                    "description": "Historical occurrences to simulate in Phase 2 validation",
                },
                "min_occurrences_required": {
                    "type": "int",
                    "range": [1, 100],
                    "default": 5,
                    "description": "Minimum prior setups required before considering tradable",
                },
                "qualification_min_wins": {
                    "type": "int",
                    "range": [1, 500],
                    "default": 21,
                    "description": "Absolute wins threshold (when lookback_occurrences data available)",
                },
                "qualification_min_win_rate": {
                    "type": "float",
                    "range": [0.0, 1.0],
                    "default": 0.70,
                    "description": "Win-rate threshold; either criterion (wins OR rate) qualifies",
                },
                "max_orb_range_pct": {
                    "type": "float",
                    "range": [0.01, 10.0],
                    "default": 1.0,
                    "description": "D+1 ORB range must be ≤ this % of OR_Close (wider = skip)",
                },
                "max_entry_time_ist": {
                    "type": "str",
                    "default": "12:00",
                    "description": "Latest candle open time (IST HH:MM) for entry",
                },
                "capital_per_trade": {
                    "type": "float",
                    "default": 100000.0,
                    "description": "Capital per trade in ₹",
                },
                "slippage_pct": {
                    "type": "float",
                    "range": [0.0, 2.0],
                    "default": 0.05,
                    "description": "Slippage % on fills",
                },
                "brokerage_per_side": {
                    "type": "float",
                    "default": 20.0,
                    "description": "Flat brokerage per trade side in ₹",
                },
            },
        )


# ── Classifier adapter ────────────────────────────────────────────────────────

class _ORHVClassifierAdapter:
    """
    Translates ORHVDetectionResult to the framework-neutral DayClassificationResult.

    strategy_signal is None because ORHV direction is determined on D+1 in real-time.
    """

    def __init__(self, detector: Any) -> None:
        self._detector = detector

    def classify(self, candles: list) -> DayClassificationResult:
        result = self._detector.detect(candles)
        return DayClassificationResult(
            is_valid=result.is_candidate,
            strategy_signal=None,           # direction unknown until D+1
            orb_high=result.orh_d,
            orb_low=result.orl_d,
            breakout_price=result.ch1_high, # the upper breakout level (informational)
            breakout_time=result.ch1_time,
            move_percent=None,
            rejection_reason=result.rejection_reason,
            candle_count=result.candle_count,
            metadata={
                "ch1_found": result.ch1_found,
                "cl1_found": result.cl1_found,
                "condition_a_met": result.condition_a_met,
                "condition_b_met": result.condition_b_met,
                "cl1_low": result.cl1_low,
            },
        )

    def detect(self, candles: list):
        """Return native ORHVDetectionResult for services that need it directly."""
        return self._detector.detect(candles)
