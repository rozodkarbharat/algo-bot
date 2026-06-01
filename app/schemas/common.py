"""
Shared Pydantic response schemas used across multiple API endpoints.
"""

from typing import Generic, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    """
    Generic paginated list response.

    Usage:
        @router.get("/stocks", response_model=PaginatedResponse[StockResponse])
    """

    items: list[T]
    total: int = Field(..., description="Total matching records in the collection")
    page: int = Field(..., description="Current page number (1-based)")
    page_size: int = Field(..., description="Records per page")
    pages: int = Field(..., description="Total number of pages")

    @classmethod
    def build(cls, items: list[T], total: int, page: int, page_size: int) -> "PaginatedResponse[T]":
        pages = max(1, (total + page_size - 1) // page_size)
        return cls(items=items, total=total, page=page, page_size=page_size, pages=pages)


class MessageResponse(BaseModel):
    """Simple acknowledgement response."""

    message: str
    success: bool = True


class ErrorResponse(BaseModel):
    """Standard error response shape (mirrors exception_handlers.py output)."""

    error: str
    message: str
    detail: Optional[object] = None
    status_code: int
