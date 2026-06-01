"""
New Strategy Template — concrete BaseStrategy implementation.

STEP-BY-STEP GUIDE:
  1. Copy this entire folder to app/strategy/strategies/your_strategy_name/
  2. Rename MyStrategy → YourStrategyName everywhere.
  3. Fill in all the TODO sections.
  4. Register in app/strategy/strategy_registry._initialize_registry():
         from app.strategy.strategies.your_strategy_name.strategy import YourStrategyName
         registry.register(YourStrategyName())
  5. Run the test suite to confirm nothing broke.
  6. Write tests in tests/test_strategy_your_strategy_name.py.
  7. Update PROJECT_CONTEXT.md with your strategy's description.
"""

from __future__ import annotations

from typing import Any, Optional

from app.strategy.base_strategy import BaseStrategy, DayClassificationResult, StrategyMetadata
from app.strategy.templates.new_strategy_template.config import MyStrategyConfig
from app.strategy.templates.new_strategy_template.constants import (
    STRATEGY_CATEGORY,
    STRATEGY_DESCRIPTION,
    STRATEGY_ID,
    STRATEGY_NAME,
    STRATEGY_VERSION,
)


class MyStrategy(BaseStrategy):    # ← RENAME THIS
    """
    TODO: One-line description of this strategy.

    Implements BaseStrategy for the <strategy name> strategy.
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
        return MyStrategyConfig().to_dict()

    def validate_configuration(self, config: dict) -> None:
        """TODO: Add range checks for all numeric parameters."""
        if "probability_threshold" in config:
            val = config["probability_threshold"]
            if not (0.0 <= val <= 1.0):
                raise ValueError(f"probability_threshold must be in [0.0, 1.0], got {val}")
        if "capital_per_trade" in config and config["capital_per_trade"] <= 0:
            raise ValueError("capital_per_trade must be > 0")
        # Add more validation ...

    # ── Engine factories ──────────────────────────────────────────────────────

    def create_day_classifier(self, config: Optional[dict] = None) -> Any:
        """
        TODO: Return a classifier object with a classify(candles) method.

        Example minimal implementation:

            class MyClassifier:
                def classify(self, candles: list) -> DayClassificationResult:
                    # Your pure classification logic here
                    ...
                    return DayClassificationResult(
                        is_valid=...,
                        strategy_signal="UP" | "DOWN" | None,
                        orb_high=candles[0].high,
                        orb_low=candles[0].low,
                    )
            return MyClassifier()
        """
        raise NotImplementedError("create_day_classifier not implemented")

    def create_backtest_engine(self, config: dict) -> Any:
        """
        TODO: Return a backtest engine with a run() method.

        The engine must implement:
            engine.run(
                symbols:       list[str],
                prob_scores:   dict[str, float],
                osd_history:   dict,   # symbol → date_str → {is_valid, signal}
                candle_history: dict,  # symbol → date_str → list[CandleData]
            ) → BacktestEngineResult

        You can subclass app.strategy.backtest_engine.BacktestEngine and
        override _build_trade_setup() for strategy-specific entry logic.
        """
        raise NotImplementedError("create_backtest_engine not implemented")

    # ── Risk calculations ─────────────────────────────────────────────────────

    def calculate_stop_loss(
        self,
        entry_price: float,
        orb_high: float,
        orb_low: float,
        side: str,
        config: Optional[dict] = None,
    ) -> float:
        """TODO: Return the stop-loss price for this strategy."""
        raise NotImplementedError("calculate_stop_loss not implemented")

    def calculate_targets(
        self,
        entry_price: float,
        orb_high: float,
        orb_low: float,
        side: str,
        config: Optional[dict] = None,
    ) -> list[float]:
        """TODO: Return list of target prices, or [] for EOD-only exit."""
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
                # TODO: Document each parameter.
                # Format: param_name → {type, default, range?, description}
                "probability_threshold": {
                    "type": "float",
                    "range": [0.0, 1.0],
                    "default": 0.65,
                    "description": "Minimum probability threshold",
                },
                "capital_per_trade": {
                    "type": "float",
                    "default": 100000.0,
                    "description": "Capital per trade in ₹",
                },
            },
        )
