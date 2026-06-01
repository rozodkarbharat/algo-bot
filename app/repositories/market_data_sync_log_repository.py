"""
MarketDataSyncLog repository — data-access layer for the sync audit trail.
"""

from typing import Optional

from app.core.exceptions import DatabaseException
from app.models.market_data_sync_log import MarketDataSyncLog, SyncStatus
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class MarketDataSyncLogRepository(BaseRepository[MarketDataSyncLog]):
    document_model = MarketDataSyncLog

    async def create_log(self, log: MarketDataSyncLog) -> MarketDataSyncLog:
        """Persist a new sync log entry."""
        return await self.create(log)

    async def update_log(self, log: MarketDataSyncLog) -> MarketDataSyncLog:
        """Persist changes to an existing log entry."""
        return await self.save(log)

    async def get_latest_log_for_symbol(
        self, symbol: str, interval: str
    ) -> Optional[MarketDataSyncLog]:
        """Return the most recent log entry for a given symbol and interval."""
        try:
            return await (
                MarketDataSyncLog.find({"symbol": symbol, "interval": interval})
                .sort("-created_at")
                .limit(1)
                .first_or_none()
            )
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch latest sync log for {symbol}.", detail=str(exc)
            )

    async def get_recent_logs(
        self,
        limit: int = 50,
        skip: int = 0,
        status: Optional[SyncStatus] = None,
    ) -> list[MarketDataSyncLog]:
        """Return recent sync logs, optionally filtered by status."""
        try:
            filt: dict = {} if status is None else {"status": status.value}
            return (
                await MarketDataSyncLog.find(filt)
                .sort("-created_at")
                .skip(skip)
                .limit(limit)
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException("Failed to list sync logs.", detail=str(exc))

    async def get_failed_symbols(self, interval: str) -> list[str]:
        """
        Return symbols whose last sync log has status=FAILED.
        Used by the retry job to re-queue only failed symbols.
        """
        try:
            logs = await MarketDataSyncLog.find(
                {"interval": interval, "status": SyncStatus.FAILED.value}
            ).to_list()
            return [log.symbol for log in logs]
        except Exception as exc:
            raise DatabaseException("Failed to fetch failed symbols.", detail=str(exc))

    async def count_by_status(self, status: SyncStatus) -> int:
        """Count log entries with a given status."""
        try:
            return await MarketDataSyncLog.find(
                {"status": status.value}
            ).count()
        except Exception as exc:
            raise DatabaseException("Failed to count logs by status.", detail=str(exc))
