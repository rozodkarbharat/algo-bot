"""
Stock API request/response schemas.

Decoupled from the Beanie Stock model so the API contract can evolve
independently of the database schema.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class StockCreate(BaseModel):
    """Payload for manually registering a new stock."""

    symbol: str = Field(..., description="NSE/BSE ticker, e.g. 'RELIANCE'")
    exchange: str = Field(default="NSE")
    instrument_token: str = Field(..., description="Angel One symboltoken")
    company_name: str
    indices: list[str] = Field(default_factory=list)
    sector: Optional[str] = None


class StockResponse(BaseModel):
    """Stock detail returned by the API."""

    symbol: str
    exchange: str
    instrument_token: str
    company_name: str
    indices: list[str]
    sector: Optional[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class StockListItem(BaseModel):
    """Compact stock representation for list endpoints."""

    symbol: str
    exchange: str
    company_name: str
    is_active: bool
    indices: list[str]

    model_config = {"from_attributes": True}
