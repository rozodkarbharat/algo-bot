"""
StrategyRegistry — central catalogue of all available trading strategies.

Strategies self-register by importing strategy_registry and calling
registry.register(strategy_instance).  The canonical registration point is
_initialize_registry() at the bottom of this module, which is called once
when app/strategy/__init__.py is first imported.

Usage:
    from app.strategy.strategy_registry import registry

    strategy = registry.get("one_side_orb")
    all_ids   = registry.strategy_ids()
    listing   = registry.list_strategies()  # → list[BaseStrategy]
"""

from __future__ import annotations

import logging
from typing import Optional

from app.strategy.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class StrategyRegistry:
    """
    Thread-safe, module-level strategy catalogue.

    Holds one instance per strategy_id.  Typically used as a singleton via
    the module-level `registry` object; direct instantiation is supported
    for isolated testing.
    """

    def __init__(self) -> None:
        self._strategies: dict[str, BaseStrategy] = {}

    # ── Registration ──────────────────────────────────────────────────────────

    def register(self, strategy: BaseStrategy) -> None:
        """
        Register a strategy instance.

        Overwrites any previously registered strategy with the same id (allows
        hot-reload in development without restarting the interpreter).

        Args:
            strategy: A concrete BaseStrategy instance.
        """
        sid = strategy.strategy_id
        already_present = sid in self._strategies
        self._strategies[sid] = strategy
        if already_present:
            logger.warning("StrategyRegistry: replaced '%s'.", sid)
        else:
            logger.info(
                "StrategyRegistry: registered '%s' v%s (%s).",
                sid, strategy.strategy_version, strategy.strategy_name,
            )

    # ── Lookup ────────────────────────────────────────────────────────────────

    def get(self, strategy_id: str) -> BaseStrategy:
        """
        Return the strategy for the given id.

        Raises:
            KeyError: if strategy_id is not registered.
        """
        if strategy_id not in self._strategies:
            available = self.strategy_ids()
            raise KeyError(
                f"Strategy '{strategy_id}' is not registered. "
                f"Available strategies: {available}"
            )
        return self._strategies[strategy_id]

    def get_or_default(
        self,
        strategy_id: Optional[str],
        default_id: str = "one_side_orb",
    ) -> BaseStrategy:
        """Return the strategy for strategy_id, falling back to default_id."""
        sid = strategy_id if strategy_id else default_id
        return self.get(sid)

    # ── Listing ───────────────────────────────────────────────────────────────

    def list_strategies(self) -> list[BaseStrategy]:
        """Return all registered strategies, sorted by strategy_id."""
        return sorted(self._strategies.values(), key=lambda s: s.strategy_id)

    def strategy_ids(self) -> list[str]:
        """Return sorted list of all registered strategy ids."""
        return sorted(self._strategies.keys())

    def is_registered(self, strategy_id: str) -> bool:
        """Return True if strategy_id is registered."""
        return strategy_id in self._strategies

    # ── Diagnostics ───────────────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self._strategies)

    def __repr__(self) -> str:
        return f"<StrategyRegistry strategies={self.strategy_ids()}>"


# ── Module-level singleton ────────────────────────────────────────────────────

registry = StrategyRegistry()


_registry_initialized = False


def _initialize_registry() -> None:
    """
    Import and register all known strategies.

    Called once from app/strategy/__init__.py on first import.
    Idempotent — subsequent calls are no-ops.

    To add a new strategy:
        1. Create app/strategy/strategies/your_id/strategy.py implementing BaseStrategy.
        2. Import and register it below.
    """
    global _registry_initialized
    if _registry_initialized:
        return
    _registry_initialized = True

    from app.strategy.strategies.one_side_orb.strategy import OneSideORBStrategy
    from app.strategy.strategies.opening_range_historical_validation.strategy import (
        OpeningRangeHistoricalValidationStrategy,
    )

    registry.register(OneSideORBStrategy())
    registry.register(OpeningRangeHistoricalValidationStrategy())

    logger.info(
        "StrategyRegistry initialized: %d strategy/ies — %s",
        len(registry),
        registry.strategy_ids(),
    )
