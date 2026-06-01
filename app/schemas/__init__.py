"""
Schemas package — Pydantic request/response models for the API layer.

Schemas are separate from Beanie Document models:
  - Documents map to MongoDB collections (persistence layer)
  - Schemas shape the HTTP API contract (presentation layer)

This separation allows the API contract to evolve independently of the
database schema, and keeps sensitive fields out of API responses.

Planned schemas:
  candle.py     — CandleCreate, CandleResponse, CandleQuery
  signal.py     — SignalResponse
  order.py      — PlaceOrderRequest (API), OrderResponse
  common.py     — PaginatedResponse, ErrorResponse
"""
