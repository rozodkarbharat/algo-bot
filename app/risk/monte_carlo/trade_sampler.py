"""
Trade Sampler — three randomized resampling strategies for Monte Carlo simulation.

All methods take a historical list of trade P&L values and return a new
sequence of the same length using a different ordering / selection mechanism.

No I/O, no DB — pure Python with stdlib only.

Methods:
  RANDOM_SHUFFLE  — Permutes the existing sequence (no repeats).
                    Preserves the exact distribution; changes temporal order.
  BOOTSTRAP       — Samples n trades independently with replacement.
                    Classic bootstrap: any trade can repeat multiple times.
                    Better at capturing tail risk via repeated bad trades.
  REPLACEMENT     — Alias for BOOTSTRAP (same semantics, distinct label for
                    semantic clarity when used in config/API).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class SamplingMethod(str, Enum):
    RANDOM_SHUFFLE = "random_shuffle"
    BOOTSTRAP = "bootstrap"
    REPLACEMENT = "replacement"


@dataclass(frozen=True)
class SampledTrades:
    pnls: list[float]
    method: SamplingMethod
    original_count: int


class TradeSampler:
    """
    Generates randomized trade sequences from a historical P&L list.

    Thread-safety: each instance has its own Random state. For reproducible
    simulations pass a fixed seed; for production randomness omit it.
    """

    def __init__(self, seed: Optional[int] = None) -> None:
        self._rng = random.Random(seed)

    def sample(
        self,
        trade_pnls: list[float],
        method: SamplingMethod = SamplingMethod.BOOTSTRAP,
        n: Optional[int] = None,
    ) -> SampledTrades:
        """
        Return one resampled trade sequence.

        Args:
            trade_pnls: Historical P&L per executed trade (NO_BREAKOUT excluded).
            method:     Sampling strategy (see module docstring).
            n:          Output length. Defaults to len(trade_pnls).

        Returns:
            SampledTrades with the resampled P&L list and metadata.
        """
        if not trade_pnls:
            return SampledTrades(pnls=[], method=method, original_count=0)

        target_n = n if n is not None else len(trade_pnls)

        if method == SamplingMethod.RANDOM_SHUFFLE:
            sampled = list(trade_pnls)
            self._rng.shuffle(sampled)
            # If n > len, pad with additional bootstrap samples
            if target_n > len(sampled):
                sampled += self._rng.choices(trade_pnls, k=target_n - len(sampled))
            else:
                sampled = sampled[:target_n]
        elif method in (SamplingMethod.BOOTSTRAP, SamplingMethod.REPLACEMENT):
            sampled = self._rng.choices(trade_pnls, k=target_n)
        else:
            raise ValueError(f"Unknown sampling method: {method}")

        return SampledTrades(
            pnls=sampled,
            method=method,
            original_count=len(trade_pnls),
        )

    def sample_batch(
        self,
        trade_pnls: list[float],
        n_simulations: int,
        method: SamplingMethod = SamplingMethod.BOOTSTRAP,
    ) -> list[SampledTrades]:
        """Generate n_simulations independent samples."""
        return [self.sample(trade_pnls, method) for _ in range(n_simulations)]
