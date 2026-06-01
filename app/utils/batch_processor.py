"""
Async batch processing utility.

Provides concurrency-controlled parallel execution with:
  - configurable semaphore (max parallel coroutines)
  - per-item error isolation (one failure doesn't abort the batch)
  - optional inter-batch delay (rate-limit guard)
  - progress callback for logging

Usage:
    results = await process_in_batches(
        items=symbols,
        processor=fetch_candles,
        concurrency=3,
        delay_seconds=0.5,
        on_progress=lambda done, total: logger.info("%d/%d", done, total),
    )
    successes = [r for r in results if not isinstance(r, Exception)]
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

T = TypeVar("T")   # input item type
R = TypeVar("R")   # result type


@dataclass
class BatchResult(Generic[T, R]):
    """Outcome of processing a single item."""

    item: T
    result: R | None = None
    error: Exception | None = None

    @property
    def success(self) -> bool:
        return self.error is None


@dataclass
class BatchSummary(Generic[T, R]):
    """Aggregate outcome of an entire batch run."""

    results: list[BatchResult[T, R]] = field(default_factory=list)

    @property
    def successful(self) -> list[BatchResult[T, R]]:
        return [r for r in self.results if r.success]

    @property
    def failed(self) -> list[BatchResult[T, R]]:
        return [r for r in self.results if not r.success]

    @property
    def success_count(self) -> int:
        return len(self.successful)

    @property
    def failure_count(self) -> int:
        return len(self.failed)

    @property
    def total(self) -> int:
        return len(self.results)


async def process_in_batches(
    items: list[T],
    processor: Callable[[T], Awaitable[R]],
    concurrency: int = 5,
    delay_seconds: float = 0.0,
    on_progress: Callable[[int, int], None] | None = None,
) -> BatchSummary[T, R]:
    """
    Process `items` concurrently with at most `concurrency` parallel tasks.

    Each item is processed independently — an exception from one does not
    cancel others. All results (successes and failures) are collected into
    the returned BatchSummary.

    Args:
        items:          Items to process.
        processor:      Async function that processes a single item.
        concurrency:    Maximum simultaneous coroutines (semaphore size).
        delay_seconds:  Sleep between each item completion (rate-limit guard).
        on_progress:    Optional callback(completed_count, total_count).
    """
    semaphore = asyncio.Semaphore(concurrency)
    summary: BatchSummary[T, R] = BatchSummary()
    completed = 0
    total = len(items)

    async def _process_one(item: T) -> BatchResult[T, R]:
        nonlocal completed
        async with semaphore:
            try:
                result = await processor(item)
                batch_result: BatchResult[T, R] = BatchResult(item=item, result=result)
            except Exception as exc:
                batch_result = BatchResult(item=item, error=exc)
            finally:
                if delay_seconds > 0:
                    await asyncio.sleep(delay_seconds)
            completed += 1
            if on_progress:
                on_progress(completed, total)
            return batch_result

    tasks = [asyncio.create_task(_process_one(item)) for item in items]
    results = await asyncio.gather(*tasks)
    summary.results = list(results)
    return summary
