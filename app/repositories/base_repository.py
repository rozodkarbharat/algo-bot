"""
Generic async repository base class.

Provides a typed CRUD foundation that concrete repositories extend.
Services depend only on the concrete repository's interface — this base
keeps the boilerplate out of each individual repository.

Pattern:
    class StockRepository(BaseRepository[Stock]):
        document_model = Stock

        async def get_by_symbol(self, symbol: str) -> Stock | None:
            return await Stock.find_one(Stock.symbol == symbol)
"""

from typing import Any, Generic, Optional, Type, TypeVar

from beanie import Document, PydanticObjectId

from app.core.exceptions import DatabaseException, DocumentNotFoundException
from app.utils.logger import get_logger

DocT = TypeVar("DocT", bound=Document)
logger = get_logger(__name__)


class BaseRepository(Generic[DocT]):
    """
    Generic Beanie repository providing common CRUD operations.

    Generic parameter DocT must be a Beanie Document subclass.
    Subclasses set the `document_model` class attribute.
    """

    document_model: Type[DocT]

    # ── Read ──────────────────────────────────────────────────────────────────

    async def get_by_id(self, doc_id: PydanticObjectId | str) -> Optional[DocT]:
        """Return a document by its MongoDB _id, or None if not found."""
        try:
            if isinstance(doc_id, str):
                doc_id = PydanticObjectId(doc_id)
            return await self.document_model.get(doc_id)
        except Exception as exc:
            logger.error("get_by_id failed for %s id=%s: %s", self.document_model.__name__, doc_id, exc)
            raise DatabaseException(f"Failed to fetch {self.document_model.__name__} by id.", detail=str(exc))

    async def get_by_id_or_raise(self, doc_id: PydanticObjectId | str) -> DocT:
        """Return a document by id, raising DocumentNotFoundException if absent."""
        doc = await self.get_by_id(doc_id)
        if doc is None:
            raise DocumentNotFoundException(self.document_model.__name__, doc_id)
        return doc

    async def get_all(self, limit: int = 100, skip: int = 0) -> list[DocT]:
        """Return up to `limit` documents with optional offset."""
        try:
            return await self.document_model.find({}).skip(skip).limit(limit).to_list()
        except Exception as exc:
            raise DatabaseException(f"Failed to list {self.document_model.__name__}.", detail=str(exc))

    async def count(self) -> int:
        """Return total document count for the collection."""
        return await self.document_model.find({}).count()

    # ── Write ─────────────────────────────────────────────────────────────────

    async def create(self, document: DocT) -> DocT:
        """Insert a new document and return the persisted instance."""
        try:
            return await document.insert()
        except Exception as exc:
            logger.error("create failed for %s: %s", self.document_model.__name__, exc)
            raise DatabaseException(f"Failed to create {self.document_model.__name__}.", detail=str(exc))

    async def save(self, document: DocT) -> DocT:
        """Insert or replace a document (upsert by _id)."""
        try:
            await document.save()
            return document
        except Exception as exc:
            logger.error("save failed for %s: %s", self.document_model.__name__, exc)
            raise DatabaseException(f"Failed to save {self.document_model.__name__}.", detail=str(exc))

    async def delete(self, document: DocT) -> None:
        """Delete a document."""
        try:
            await document.delete()
        except Exception as exc:
            raise DatabaseException(f"Failed to delete {self.document_model.__name__}.", detail=str(exc))

    async def delete_by_id(self, doc_id: PydanticObjectId | str) -> bool:
        """Delete by id; returns True if a document was deleted, False if not found."""
        doc = await self.get_by_id(doc_id)
        if doc is None:
            return False
        await self.delete(doc)
        return True
