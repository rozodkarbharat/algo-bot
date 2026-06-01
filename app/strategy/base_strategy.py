"""
BaseStrategy — abstract contract every trading strategy must implement.

All trading strategies are plug-and-play modules that implement this class.
Services interact with strategies exclusively through this interface so no
engine (backtest, live, paper, analytics) needs to know about a specific strategy.

Architecture contract:
  - Strategies contain PURE LOGIC ONLY — no database I/O, no broker imports.
  - Services pre-fetch data and pass it in; strategies compute and return results.
  - Strategy identity is declared as properties, not constructor arguments.

Usage:
    from app.strategy.strategy_registry import registry

    strategy = registry.get("one_side_orb")
    engine   = strategy.create_backtest_engine(config_dict)
    result   = engine.run(symbols, prob_scores, osd_history, candle_history)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional


# ── Strategy metadata ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class StrategyMetadata:
    """
    Immutable descriptor returned by BaseStrategy.get_metadata().

    Used by the strategies API endpoint and the registry listing.
    """

    strategy_id: str
    strategy_name: str
    version: str = "1.0.0"
    description: str = ""
    category: str = "momentum"   # momentum | mean_reversion | arbitrage | statistical
    parameters: dict = field(default_factory=dict)
    # parameter schema: param_name → {type, default, range?, description}


# ── Standardised day-classification result ────────────────────────────────────

@dataclass(frozen=True)
class DayClassificationResult:
    """
    Strategy-neutral result from a single-day pattern classifier.

    Replaces the One-Side ORB-specific OneSideDetectionResult at the
    framework level so future strategies can return their own signals
    through the same pipeline.

    For One-Side ORB:
        is_valid         → is_one_side
        strategy_signal  → "UP" | "DOWN" | None
    """

    is_valid: bool
    strategy_signal: Optional[str]        # strategy-defined label ("UP", "DOWN", etc.)
    orb_high: float
    orb_low: float
    breakout_price: Optional[float] = None
    breakout_time: Optional[datetime] = None
    move_percent: Optional[float] = None
    rejection_reason: Optional[str] = None
    candle_count: int = 0
    metadata: dict = field(default_factory=dict)


# ── Base strategy ─────────────────────────────────────────────────────────────

class BaseStrategy(ABC):
    """
    Abstract base class for all trading strategies.

    Subclasses must implement every abstract method/property.
    Optional override methods have sensible defaults.

    Engine factories (create_*) return pure-Python engine objects — they
    are synchronous and safe to call from the event loop.  The returned
    engines do NOT do any I/O; all I/O is handled by the calling service.
    """

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    @abstractmethod
    def strategy_id(self) -> str:
        """Unique machine-readable identifier, e.g. 'one_side_orb'.
        Must be a valid Python identifier and URL path segment."""

    @property
    @abstractmethod
    def strategy_name(self) -> str:
        """Human-readable display name, e.g. 'One-Side ORB'."""

    @property
    @abstractmethod
    def strategy_version(self) -> str:
        """Semantic version string, e.g. '1.0.0'."""

    # ── Configuration ─────────────────────────────────────────────────────────

    @abstractmethod
    def get_default_config(self) -> dict:
        """
        Return the default configuration for this strategy as a plain dict.

        Keys must be valid Python identifiers.  Values must be JSON-serialisable.
        The dict is stored in BacktestRun.configuration for reproducibility.
        """

    @abstractmethod
    def validate_configuration(self, config: dict) -> None:
        """
        Validate a configuration dict.

        Must raise ValueError with a human-readable message if any parameter
        is out-of-range or has the wrong type.  Silently ignores unknown keys
        (forward-compatibility).
        """

    # ── Engine factories ──────────────────────────────────────────────────────

    @abstractmethod
    def create_day_classifier(self, config: Optional[dict] = None) -> Any:
        """
        Return a strategy-specific single-day pattern classifier.

        The returned object must implement:
            classify(candles: list[CandleData]) -> DayClassificationResult

        For One-Side ORB this returns an OneSideDayDetector wrapped so its
        output conforms to DayClassificationResult.

        Args:
            config: Optional parameter overrides.  None → use defaults.
        """

    @abstractmethod
    def create_backtest_engine(self, config: dict) -> Any:
        """
        Return a strategy-specific historical-replay engine.

        The returned object must implement:
            run(
                symbols: list[str],
                prob_scores: dict[str, float],
                osd_history: dict,
                candle_history: dict,
            ) -> BacktestEngineResult

        For One-Side ORB this returns a BacktestEngine(BacktestConfig).

        Args:
            config: Full configuration dict (from get_default_config() plus
                    any user overrides merged in by the calling service).
        """

    # ── Risk calculations ─────────────────────────────────────────────────────

    @abstractmethod
    def calculate_stop_loss(
        self,
        entry_price: float,
        orb_high: float,
        orb_low: float,
        side: str,              # "LONG" or "SHORT"
        config: Optional[dict] = None,
    ) -> float:
        """
        Compute the stop-loss price for a trade.

        Args:
            entry_price: Actual fill price (including slippage).
            orb_high:    High of the opening-range candle.
            orb_low:     Low of the opening-range candle.
            side:        "LONG" or "SHORT".
            config:      Strategy parameters.  None → use defaults.

        Returns:
            Stop-loss price as a float.
        """

    @abstractmethod
    def calculate_targets(
        self,
        entry_price: float,
        orb_high: float,
        orb_low: float,
        side: str,
        config: Optional[dict] = None,
    ) -> list[float]:
        """
        Compute profit-target prices for a trade (ascending for LONG, descending for SHORT).

        Returns an empty list if the strategy does not use fixed targets (e.g. EOD-only exit).
        """

    # ── Metadata ──────────────────────────────────────────────────────────────

    @abstractmethod
    def get_metadata(self) -> StrategyMetadata:
        """Return a fully populated StrategyMetadata descriptor."""

    # ── Default implementations (override when needed) ────────────────────────

    def to_dict(self) -> dict:
        """Serialise strategy identity to a dict for API responses."""
        meta = self.get_metadata()
        return {
            "strategy_id": self.strategy_id,
            "strategy_name": self.strategy_name,
            "version": self.strategy_version,
            "description": meta.description,
            "category": meta.category,
            "parameters": meta.parameters,
            "default_config": self.get_default_config(),
        }

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} id={self.strategy_id!r} v{self.strategy_version}>"
