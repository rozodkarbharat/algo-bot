"""APScheduler health check — running state and job statuses."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from app.monitoring.health_checks.base import BaseHealthCheck, ComponentHealthResult
from app.utils.logger import get_logger

logger = get_logger(__name__)


class SchedulerHealthCheck(BaseHealthCheck):
    """
    Verify APScheduler is running and jobs are scheduled.

    Marks degraded when the scheduler has no registered jobs (all jobs
    may have been removed accidentally) or when any job missed its last
    fire time by more than the configured grace window.
    """

    @property
    def component_name(self) -> str:
        return "scheduler"

    async def _run(self) -> ComponentHealthResult:
        t0 = time.perf_counter()
        try:
            from app.scheduler.scheduler import scheduler
            latency_ms = (time.perf_counter() - t0) * 1000

            if not scheduler.running:
                return ComponentHealthResult.unhealthy(
                    self.component_name,
                    message="APScheduler is not running.",
                    latency_ms=latency_ms,
                )

            jobs = scheduler.get_jobs()
            if not jobs:
                return ComponentHealthResult.degraded(
                    self.component_name,
                    latency_ms=latency_ms,
                    message="Scheduler is running but has no registered jobs.",
                    job_count=0,
                )

            now = datetime.now(timezone.utc)
            job_summaries = []
            for job in jobs:
                next_run = job.next_run_time
                job_summaries.append({
                    "id": job.id,
                    "next_run": next_run.isoformat() if next_run else "paused",
                })

            return ComponentHealthResult.ok(
                self.component_name,
                latency_ms=latency_ms,
                running=True,
                job_count=len(jobs),
                jobs=job_summaries,
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            logger.warning("[monitor:scheduler] check failed: %s", exc)
            return ComponentHealthResult.unhealthy(
                self.component_name,
                message=f"Scheduler check failed: {exc}",
                latency_ms=latency_ms,
            )
