"""Broker reconciliation engine health check — staleness and open mismatch detection."""

from __future__ import annotations

import time
from datetime import timedelta

from app.monitoring.health_checks.base import BaseHealthCheck, ComponentHealthResult
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Alert thresholds
MAX_STALE_HOURS = 4          # >4 h since last run during market hours → degraded
LOOKBACK_HOURS = 24          # Window for counting "runs today"


class ReconciliationHealthCheck(BaseHealthCheck):
    """
    Check the broker reconciliation engine health.

    Examines:
      - Whether a reconciliation run has occurred in the last 24 hours
      - Staleness: last completed run age vs. market-hours threshold
      - Count of open (DETECTED) discrepancies across all runs
    """

    @property
    def component_name(self) -> str:
        return "reconciliation_engine"

    async def _run(self) -> ComponentHealthResult:  # noqa: PLR0911
        t0 = time.perf_counter()
        try:
            from app.repositories.broker_reconciliation_repository import (
                BrokerDiscrepancyRepository,
                BrokerReconciliationRunRepository,
            )
        except ImportError as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            logger.warning("[monitor:reconciliation] import failed: %s", exc)
            return ComponentHealthResult.degraded(
                self.component_name,
                latency_ms=latency_ms,
                message="Reconciliation repository not available",
            )

        try:
            from app.utils.market_time import is_market_open, now_utc
            from app.models.broker_reconciliation import ReconciliationRunStatus

            run_repo = BrokerReconciliationRunRepository()
            discrepancy_repo = BrokerDiscrepancyRepository()

            now = now_utc()
            cutoff_24h = now - timedelta(hours=LOOKBACK_HOURS)

            # ── Fetch recent runs (last 24 h) ─────────────────────────────────
            # list_recent returns runs sorted by started_at descending; fetch a
            # generous page — reconciliation typically runs a handful of times
            # per day so 50 is more than sufficient to cover 24 h.
            recent_runs = await run_repo.list_recent(limit=50)
            runs_in_window = [r for r in recent_runs if r.started_at >= cutoff_24h]
            total_runs_today = len(runs_in_window)

            # ── Latest completed run ──────────────────────────────────────────
            latest_completed = await run_repo.get_latest_completed()

            last_run_at: str | None = None
            hours_since_last_run: float | None = None

            if latest_completed is not None:
                last_run_at = latest_completed.started_at.isoformat()
                hours_since_last_run = round(
                    (now - latest_completed.started_at).total_seconds() / 3600, 2
                )

            # ── Open / unresolved discrepancies ───────────────────────────────
            open_mismatches = await discrepancy_repo.count_detected()

            latency_ms = (time.perf_counter() - t0) * 1000

            meta = {
                "last_run_at": last_run_at,
                "total_runs_today": total_runs_today,
                "mismatches_found": sum(
                    r.discrepancies_found for r in runs_in_window
                ),
                "open_mismatches": open_mismatches,
            }

            # ── Decision tree ─────────────────────────────────────────────────

            # Unhealthy: active mismatches require immediate attention regardless
            # of market hours.
            if open_mismatches > 0:
                return ComponentHealthResult.unhealthy(
                    self.component_name,
                    message=f"{open_mismatches} open reconciliation mismatch"
                    f"{'es' if open_mismatches != 1 else ''} found",
                    **meta,
                )

            # Degraded: stale reconciliation during market hours.
            in_market_hours = is_market_open(now)
            if in_market_hours:
                if latest_completed is None or hours_since_last_run > MAX_STALE_HOURS:
                    stale_desc = (
                        f"{hours_since_last_run:.1f} hours ago"
                        if hours_since_last_run is not None
                        else "never"
                    )
                    return ComponentHealthResult.degraded(
                        self.component_name,
                        latency_ms=latency_ms,
                        message=f"Reconciliation last ran {stale_desc}",
                        **meta,
                    )
            else:
                # Outside market hours: no recent run is expected — flag as a
                # note in metadata but return healthy.
                if latest_completed is None:
                    meta["note"] = (
                        "No completed reconciliation run on record; "
                        "normal outside market hours."
                    )
                elif hours_since_last_run is not None and hours_since_last_run > MAX_STALE_HOURS:
                    meta["note"] = (
                        f"Last run {hours_since_last_run:.1f} h ago; "
                        "acceptable outside market hours."
                    )

            return ComponentHealthResult.ok(
                self.component_name, latency_ms=latency_ms, **meta
            )

        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            logger.warning("[monitor:reconciliation] check failed: %s", exc)
            return ComponentHealthResult.unhealthy(
                self.component_name,
                message=f"Reconciliation check failed: {exc}",
                latency_ms=latency_ms,
            )
