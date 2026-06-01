"""
Capital allocation engine.

Given a set of ranked signals and a portfolio capital budget, determines
exactly how much capital to assign to each approved trade.

Three allocation methods are supported:

1. EQUAL_WEIGHT
   ─────────────
   All approved signals receive the same notional capital.

     allocated = available_capital / n_approved_signals

   Capped by PORTFOLIO_MAX_CAPITAL_PER_TRADE_PCT.

2. SCORE_WEIGHTED
   ───────────────
   Capital is distributed proportionally to each signal's ranking_score.

     weight_i = score_i / Σ(scores)
     allocated_i = available_capital * weight_i

   Capped by PORTFOLIO_MAX_CAPITAL_PER_TRADE_PCT.

3. FIXED_RISK
   ──────────
   Capital is sized so that a stop-loss hit risks exactly
   PORTFOLIO_FIXED_RISK_PCT of total capital.

     risk_amount = total_capital * risk_pct
     risk_per_share = |entry_price - stop_loss|
     shares = risk_amount / risk_per_share
     allocated = shares * entry_price

   Capped by PORTFOLIO_MAX_CAPITAL_PER_TRADE_PCT.

This module is pure (no async / no I/O). All callers supply inputs directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.models.portfolio_allocation import AllocationMethod
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Input / output types ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class AllocationInput:
    """One ranked signal that is a candidate for capital allocation."""

    signal_id: str
    symbol: str
    strategy_id: str
    ranking_score: float       # [0, 1] from SignalRanker
    entry_price: float
    stop_loss: float


@dataclass(frozen=True)
class AllocationResult:
    """Capital allocation decision for one signal."""

    signal_id: str
    symbol: str
    strategy_id: str
    method: AllocationMethod
    allocated_capital: float   # ₹ to deploy
    allocation_percent: float  # fraction of total_capital [0, 1]
    ranking_score: float
    rejection_reason: Optional[str] = None  # set when allocated_capital == 0


# ── Allocator ─────────────────────────────────────────────────────────────────

class CapitalAllocator:
    """
    Stateless capital allocation engine.

    Parameters
    ----------
    total_capital : float
        Total portfolio capital (₹). Used for percentage calculations and
        FIXED_RISK sizing.
    max_capital_per_trade_pct : float
        Hard cap on a single allocation as a fraction of total_capital [0, 1].
    fixed_risk_pct : float
        Fraction of total_capital to risk per trade when using FIXED_RISK [0, 1].
    min_capital_per_trade : float
        Minimum viable allocation. Signals sized below this are rejected.
    """

    def __init__(
        self,
        total_capital: float,
        max_capital_per_trade_pct: float = 0.20,
        fixed_risk_pct: float = 0.01,
        min_capital_per_trade: float = 1_000.0,
    ) -> None:
        self._total = total_capital
        self._max_per_trade = total_capital * max_capital_per_trade_pct
        self._fixed_risk_amount = total_capital * fixed_risk_pct
        self._min_capital = min_capital_per_trade

    # ── Public API ────────────────────────────────────────────────────────────

    def allocate(
        self,
        candidates: list[AllocationInput],
        available_capital: float,
        method: AllocationMethod,
    ) -> list[AllocationResult]:
        """
        Allocate available_capital across the given candidates.

        Returns one `AllocationResult` per input (preserving order). Signals
        that cannot be allocated a viable amount receive `allocated_capital=0`
        and a `rejection_reason`.
        """
        if not candidates:
            return []

        if method is AllocationMethod.EQUAL_WEIGHT:
            return self._equal_weight(candidates, available_capital)
        elif method is AllocationMethod.SCORE_WEIGHTED:
            return self._score_weighted(candidates, available_capital)
        elif method is AllocationMethod.FIXED_RISK:
            return self._fixed_risk(candidates, available_capital)
        else:
            raise ValueError(f"Unknown allocation method: {method}")

    # ── Methods ───────────────────────────────────────────────────────────────

    def _equal_weight(
        self, candidates: list[AllocationInput], available: float
    ) -> list[AllocationResult]:
        n = len(candidates)
        raw = available / n
        per_trade = min(raw, self._max_per_trade)

        results = []
        for inp in candidates:
            if per_trade < self._min_capital:
                results.append(self._reject(inp, AllocationMethod.EQUAL_WEIGHT, "capital_below_minimum"))
            else:
                results.append(AllocationResult(
                    signal_id=inp.signal_id,
                    symbol=inp.symbol,
                    strategy_id=inp.strategy_id,
                    method=AllocationMethod.EQUAL_WEIGHT,
                    allocated_capital=round(per_trade, 2),
                    allocation_percent=round(per_trade / self._total, 6),
                    ranking_score=inp.ranking_score,
                ))
        return results

    def _score_weighted(
        self, candidates: list[AllocationInput], available: float
    ) -> list[AllocationResult]:
        total_score = sum(inp.ranking_score for inp in candidates)

        # Guard: if all scores are zero fall back to equal weight.
        if total_score <= 0:
            logger.warning("[allocator] all ranking scores are 0; falling back to equal-weight")
            return self._equal_weight(candidates, available)

        results = []
        for inp in candidates:
            weight = inp.ranking_score / total_score
            raw = available * weight
            per_trade = min(raw, self._max_per_trade)
            if per_trade < self._min_capital:
                results.append(self._reject(inp, AllocationMethod.SCORE_WEIGHTED, "capital_below_minimum"))
            else:
                results.append(AllocationResult(
                    signal_id=inp.signal_id,
                    symbol=inp.symbol,
                    strategy_id=inp.strategy_id,
                    method=AllocationMethod.SCORE_WEIGHTED,
                    allocated_capital=round(per_trade, 2),
                    allocation_percent=round(per_trade / self._total, 6),
                    ranking_score=inp.ranking_score,
                ))
        return results

    def _fixed_risk(
        self, candidates: list[AllocationInput], available: float
    ) -> list[AllocationResult]:
        results = []
        for inp in candidates:
            risk_per_share = abs(inp.entry_price - inp.stop_loss)
            if risk_per_share <= 0:
                results.append(self._reject(inp, AllocationMethod.FIXED_RISK, "zero_risk_per_share"))
                continue

            shares = self._fixed_risk_amount / risk_per_share
            raw = shares * inp.entry_price
            per_trade = min(raw, self._max_per_trade, available)

            if per_trade < self._min_capital:
                results.append(self._reject(inp, AllocationMethod.FIXED_RISK, "capital_below_minimum"))
            else:
                results.append(AllocationResult(
                    signal_id=inp.signal_id,
                    symbol=inp.symbol,
                    strategy_id=inp.strategy_id,
                    method=AllocationMethod.FIXED_RISK,
                    allocated_capital=round(per_trade, 2),
                    allocation_percent=round(per_trade / self._total, 6),
                    ranking_score=inp.ranking_score,
                ))
        return results

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _reject(
        inp: AllocationInput, method: AllocationMethod, reason: str
    ) -> AllocationResult:
        return AllocationResult(
            signal_id=inp.signal_id,
            symbol=inp.symbol,
            strategy_id=inp.strategy_id,
            method=method,
            allocated_capital=0.0,
            allocation_percent=0.0,
            ranking_score=inp.ranking_score,
            rejection_reason=reason,
        )

    @property
    def total_capital(self) -> float:
        return self._total

    @property
    def max_capital_per_trade(self) -> float:
        return self._max_per_trade

    @property
    def fixed_risk_amount(self) -> float:
        return self._fixed_risk_amount
