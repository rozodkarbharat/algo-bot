"""
Data sync API request/response schemas.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.market_data_sync_log import SyncStatus


class HistoricalSyncRequest(BaseModel):
    """
    Payload for POST /api/v1/sync/historical-data.

    `symbols` is optional — omit it to sync all active NIFTY stocks.
    """

    from_date: str = Field(..., description="Start date YYYY-MM-DD")
    to_date: str = Field(..., description="End date YYYY-MM-DD")
    interval: str = Field(
        default="FIFTEEN_MINUTE",
        description="CandleInterval value",
    )
    symbols: Optional[list[str]] = Field(
        None,
        description="Specific symbols to sync. None = all active stocks.",
    )
    force_refetch: bool = Field(
        default=False,
        description="Re-fetch and overwrite dates that already exist in DB",
    )


class SyncResultResponse(BaseModel):
    """Summary returned after a sync operation completes."""

    total_symbols: int
    successful: int
    failed: int
    skipped: int
    records_inserted: int
    duration_seconds: float
    failed_symbols: list[str]


class SyncLogResponse(BaseModel):
    """A single sync audit log entry."""

    id: Optional[str] = None
    symbol: str
    exchange: str
    interval: str
    sync_from: datetime
    sync_to: datetime
    sync_end: Optional[datetime]
    records_inserted: int
    records_skipped: int
    status: SyncStatus
    error_message: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}
