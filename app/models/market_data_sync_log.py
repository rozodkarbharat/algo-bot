"""
Market data synchronisation audit log.

Every ingestion run creates one log entry per symbol. The log is used for:
  - Monitoring ingestion health (success/failure rates)
  - Resuming partial ingestions (last successful date per symbol)
  - Debugging API errors
  - Driving the scheduler's "smart skip" logic
"""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Optional

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SyncStatus(StrEnum):
    PENDING = "PENDING"      # created but not started
    RUNNING = "RUNNING"      # actively fetching
    SUCCESS = "SUCCESS"      # all requested dates ingested
    PARTIAL = "PARTIAL"      # some dates failed, some succeeded
    FAILED = "FAILED"        # complete failure (no data inserted)
    SKIPPED = "SKIPPED"      # all dates already existed; nothing to do


class MarketDataSyncLog(Document):
    """
    One record per ingestion attempt for a single symbol.

    Collection: market_data_sync_logs
    """

    symbol: str = Field(..., description="Ticker symbol being synced")
    exchange: str = Field(default="NSE")
    interval: str = Field(..., description="CandleInterval value used for this sync")

    # Date range this sync covers (UTC midnight datetimes).
    sync_from: datetime = Field(..., description="Start of requested date range (UTC)")
    sync_to: datetime = Field(..., description="End of requested date range (UTC)")

    # Populated on completion.
    sync_end: Optional[datetime] = Field(
        None, description="Wall-clock timestamp when the sync finished"
    )
    records_inserted: int = Field(
        default=0, description="Number of new daily buckets written to MongoDB"
    )
    records_skipped: int = Field(
        default=0, description="Dates that already existed and were skipped"
    )
    status: SyncStatus = Field(default=SyncStatus.PENDING)
    error_message: Optional[str] = Field(None, description="Last error detail if status=FAILED")

    created_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "market_data_sync_logs"
        indexes = [
            IndexModel([("symbol", ASCENDING)]),
            IndexModel([("interval", ASCENDING)]),
            IndexModel([("status", ASCENDING)]),
            # Most-recent-log-per-symbol query
            IndexModel(
                [("symbol", ASCENDING), ("created_at", DESCENDING)],
                name="symbol_created_desc",
            ),
        ]

    def mark_running(self) -> None:
        self.status = SyncStatus.RUNNING

    def mark_success(self, inserted: int, skipped: int) -> None:
        self.status = SyncStatus.SUCCESS
        self.records_inserted = inserted
        self.records_skipped = skipped
        self.sync_end = _utcnow()

    def mark_partial(self, inserted: int, skipped: int, error: str) -> None:
        self.status = SyncStatus.PARTIAL
        self.records_inserted = inserted
        self.records_skipped = skipped
        self.error_message = error
        self.sync_end = _utcnow()

    def mark_failed(self, error: str) -> None:
        self.status = SyncStatus.FAILED
        self.error_message = error
        self.sync_end = _utcnow()

    def mark_skipped(self, skipped: int) -> None:
        self.status = SyncStatus.SKIPPED
        self.records_skipped = skipped
        self.sync_end = _utcnow()
