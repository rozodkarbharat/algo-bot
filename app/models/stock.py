"""
Stock instrument master document.

One document per tradeable instrument. Serves as the authoritative registry
for all symbols the system tracks. The instrument_token field is the Angel One
(and later, other broker) token used in market-data API calls.
"""

from datetime import datetime, timezone
from typing import Annotated, Optional

from beanie import Document, Indexed
from pydantic import Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Stock(Document):
    """
    Represents a single tradeable stock/instrument.

    Collection: stocks
    """

    # NSE/BSE ticker symbol, e.g. "RELIANCE"
    symbol: Annotated[str, Indexed(unique=True)] = Field(
        ..., description="Exchange ticker symbol"
    )
    exchange: str = Field(default="NSE", description="Exchange: NSE | BSE | NFO")

    # Angel One's internal token — required for historical-data API calls.
    # Obtain from: https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json
    instrument_token: Annotated[str, Indexed(unique=True)] = Field(
        ..., description="Broker instrument token (Angel One symboltoken)"
    )
    company_name: str = Field(..., description="Full company name")

    # Universe membership — lets the system scope jobs to specific indices.
    # e.g. ["NIFTY50", "NIFTY100"]
    indices: list[str] = Field(
        default_factory=list,
        description="Index memberships: NIFTY50, NIFTY100, NIFTY200, etc.",
    )
    sector: Optional[str] = Field(None, description="GICS sector, e.g. 'Information Technology'")

    is_active: bool = Field(default=True, description="Whether to include in data ingestion")
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "stocks"
        # Indexes beyond the Indexed annotations above.
        # Compound and non-unique indexes go here.
        indexes: list = [
            # Fast lookup of active instruments in a given universe.
            # Motor will create this on startup via Beanie.
        ]

    def mark_updated(self) -> None:
        """Update the updated_at timestamp before saving."""
        self.updated_at = _utcnow()
