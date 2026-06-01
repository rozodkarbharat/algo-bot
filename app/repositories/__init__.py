"""
Repositories package — data-access layer.

Each repository wraps Beanie CRUD operations for a single Document type.
Services depend on repository interfaces, not on Beanie directly, which
makes the business logic testable without a running MongoDB.

Pattern:
    class BaseRepository(Generic[T]):
        async def get_by_id(self, id: PydanticObjectId) -> T | None: ...
        async def create(self, doc: T) -> T: ...
        async def update(self, id, data) -> T | None: ...
        async def delete(self, id) -> bool: ...

Planned repositories:
  candle_repository.py
  signal_repository.py
  order_repository.py
"""
