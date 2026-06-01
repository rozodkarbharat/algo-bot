"""
OneSideORBStrategy — concrete implementation of BaseStrategy for One-Side ORB.

This class is the single plug-in point for the One-Side ORB strategy in the
multi-strategy framework.  It does NOT duplicate any business logic; instead
it delegates to the battle-tested pure engines already in app/strategy/:

    one_side_detector.py        → OneSideDayDetector  (day classification)
    continuation_probability.py → ContinuationProbabilityEngine  (probability)
    backtest_engine.py          → BacktestEngine  (historical replay)
    trade_simulator.py          → TradeSimulator  (per-trade simulation)

All existing services and tests that import those modules directly continue to
work without modification — this class is additive infrastructure only.

Registration:
    Imported and registered by strategy_registry._initialize_registry() which
    is called once when app/strategy/__init__.py is first imported at startup.
"""

from __future__ import annotations

from typing import Any, Optional

from app.strategy.base_strategy import BaseStrategy, DayClassificationResult, StrategyMetadata
from app.strategy.strategies.one_side_orb.config import OneSideORBConfig
from app.strategy.strategies.one_side_orb.constants import (
    STRATEGY_CATEGORY,
    STRATEGY_DESCRIPTION,
    STRATEGY_ID,
    STRATEGY_NAME,
    STRATEGY_VERSION,
)


class OneSideORBStrategy(BaseStrategy):
    """
    One-Side Opening Range Breakout strategy.

    Detects stocks with a confirmed one-directional first 15-minute candle
    (one-side day), filters by historical continuation probability, and
    trades ORB breakouts with stop-loss at the opposite ORB boundary.
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
        return OneSideORBConfig().to_dict()

    def validate_configuration(self, config: dict) -> None:
        """
        Validate an OneSideORB config dict.

        Raises ValueError if any parameter is out of bounds.
        Silently ignores unknown keys (forward-compatibility).
        """
        checks = {
            "probability_threshold": (0.0, 1.0),
            "min_move_percent": (0.01, 10.0),
            "max_orb_range_pct": (0.01, 10.0),
            "sl_buffer_pct": (0.0, 5.0),
            "slippage_pct": (0.0, 2.0),
            "lookback_days": (30, 2520),
            "min_occurrences": (1, 500),
        }
        for key, (lo, hi) in checks.items():
            if key in config:
                val = config[key]
                if not (lo <= val <= hi):
                    raise ValueError(
                        f"'{key}' must be in [{lo}, {hi}], got {val}."
                    )

        if "capital_per_trade" in config and config["capital_per_trade"] <= 0:
            raise ValueError("'capital_per_trade' must be > 0.")

        if "brokerage_per_side" in config and config["brokerage_per_side"] < 0:
            raise ValueError("'brokerage_per_side' must be >= 0.")

        if "max_entry_time_ist" in config:
            t = config["max_entry_time_ist"]
            parts = str(t).split(":")
            if len(parts) != 2 or not all(p.isdigit() for p in parts):
                raise ValueError(
                    f"'max_entry_time_ist' must be HH:MM format, got '{t}'."
                )

    # ── Engine factories ──────────────────────────────────────────────────────

    def create_day_classifier(self, config: Optional[dict] = None) -> "_ORBClassifierAdapter":
        """
        Return an OneSideDayDetector wrapped in a DayClassificationResult adapter.

        The adapter's classify() method translates OneSideDetectionResult to
        DayClassificationResult so callers don't need to know about the ORB-
        specific result type.
        """
        from app.strategy.one_side_detector import OneSideDayDetector

        cfg = OneSideORBConfig.from_dict(config or {})
        detector = OneSideDayDetector(min_move_percent=cfg.min_move_percent)
        return _ORBClassifierAdapter(detector)

    def create_backtest_engine(self, config: dict) -> Any:
        """
        Return a BacktestEngine instance configured from config dict.

        The returned engine exposes the standard interface:
            engine.run(symbols, prob_scores, osd_history, candle_history)
                → BacktestEngineResult

        Args:
            config: Dict containing at minimum 'from_date' and 'to_date'
                    (as date objects or ISO strings).  All other keys fall
                    back to OneSideORBConfig defaults.
        """
        from datetime import date
        from app.strategy.backtest_engine import BacktestConfig, BacktestEngine

        def _as_date(val: Any) -> date:
            if isinstance(val, date):
                return val
            from datetime import datetime as dt
            return dt.strptime(str(val), "%Y-%m-%d").date()

        bc = BacktestConfig(
            from_date=_as_date(config.get("from_date", date.today())),
            to_date=_as_date(config.get("to_date", date.today())),
            symbols=config.get("symbols"),
            probability_threshold=config.get("probability_threshold", 0.70),
            min_move_percent=config.get("min_move_percent", 1.0),
            max_orb_range_pct=config.get("max_orb_range_pct", 1.0),
            max_entry_time_ist=config.get("max_entry_time_ist", "11:30"),
            capital_per_trade=config.get("capital_per_trade", 100_000.0),
            slippage_pct=config.get("slippage_pct", 0.05),
            brokerage_per_side=config.get("brokerage_per_side", 20.0),
            sl_buffer_pct=config.get("sl_buffer_pct", 0.0),
        )
        return BacktestEngine(bc)

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
        One-Side ORB stop loss: opposite side of the opening range.

        LONG  → stop at orb_low  (adjusted down by sl_buffer_pct)
        SHORT → stop at orb_high (adjusted up   by sl_buffer_pct)
        """
        sl_buffer_pct = float((config or {}).get("sl_buffer_pct", 0.0))
        if side.upper() == "LONG":
            return round(orb_low * (1.0 - sl_buffer_pct / 100.0), 4)
        return round(orb_high * (1.0 + sl_buffer_pct / 100.0), 4)

    def calculate_targets(
        self,
        entry_price: float,
        orb_high: float,
        orb_low: float,
        side: str,
        config: Optional[dict] = None,
    ) -> list[float]:
        """One-Side ORB uses EOD exit — no fixed targets."""
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
                "probability_threshold": {
                    "type": "float",
                    "range": [0.0, 1.0],
                    "default": 0.70,
                    "description": "Minimum continuation probability to trade a stock (0–1)",
                },
                "min_move_percent": {
                    "type": "float",
                    "range": [0.01, 10.0],
                    "default": 1.0,
                    "description": "Min % move from ORB boundary for one-side day classification",
                },
                "max_orb_range_pct": {
                    "type": "float",
                    "range": [0.01, 10.0],
                    "default": 1.0,
                    "description": "Skip setup if first-candle range exceeds this %",
                },
                "max_entry_time_ist": {
                    "type": "str",
                    "default": "11:30",
                    "description": "Latest entry time in IST (HH:MM); entries after this are skipped",
                },
                "capital_per_trade": {
                    "type": "float",
                    "default": 100000.0,
                    "description": "Capital allocated per trade in ₹",
                },
                "slippage_pct": {
                    "type": "float",
                    "range": [0.0, 2.0],
                    "default": 0.05,
                    "description": "Expected slippage % on entry and exit fills",
                },
                "brokerage_per_side": {
                    "type": "float",
                    "default": 20.0,
                    "description": "Flat brokerage per trade side in ₹",
                },
                "sl_buffer_pct": {
                    "type": "float",
                    "range": [0.0, 5.0],
                    "default": 0.0,
                    "description": "Extra % buffer beyond ORB boundary for stop loss",
                },
                "lookback_days": {
                    "type": "int",
                    "range": [30, 2520],
                    "default": 252,
                    "description": "Trading-day lookback for continuation probability (252 ≈ 1 yr)",
                },
                "min_occurrences": {
                    "type": "int",
                    "range": [1, 500],
                    "default": 10,
                    "description": "Minimum OSD occurrences before continuation stats are reliable",
                },
            },
        )


# ── Classifier adapter ────────────────────────────────────────────────────────

class _ORBClassifierAdapter:
    """
    Adapts OneSideDayDetector to the generic DayClassificationResult contract.

    StrategyService calls classifier.classify(candles) and expects a
    DayClassificationResult regardless of which strategy is in use.
    """

    def __init__(self, detector: Any) -> None:
        self._detector = detector

    def classify(self, candles: list) -> DayClassificationResult:
        """Run OneSideDayDetector and translate to DayClassificationResult."""
        result = self._detector.detect(candles)
        return DayClassificationResult(
            is_valid=result.is_one_side,
            strategy_signal=result.direction,
            orb_high=result.first_candle_high,
            orb_low=result.first_candle_low,
            breakout_price=result.breakout_price,
            breakout_time=result.breakout_time,
            move_percent=result.move_percent,
            rejection_reason=result.rejection_reason,
            candle_count=result.candle_count,
            metadata={"opposite_side_crossed": result.opposite_side_crossed},
        )

    # Proxy the underlying detector for callers that need native result type
    def detect(self, candles: list):
        """Return the native OneSideDetectionResult (for backward compatibility)."""
        return self._detector.detect(candles)
