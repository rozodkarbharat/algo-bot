"""
PaperAccount repository — single-document state per account_id.

The account row is mutated frequently (every fill, every close, every
mark-to-market batch). All updates go through the service layer; this
repo exposes plain get/upsert primitives.
"""

from typing import Optional

from app.core.exceptions import DatabaseException
from app.models.paper_account import DEFAULT_PAPER_ACCOUNT_ID, PaperAccount
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class PaperAccountRepository(BaseRepository[PaperAccount]):
    document_model = PaperAccount

    async def get_by_account_id(
        self, account_id: str = DEFAULT_PAPER_ACCOUNT_ID
    ) -> Optional[PaperAccount]:
        try:
            return await PaperAccount.find_one({"account_id": account_id})
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch PaperAccount {account_id}.", detail=str(exc)
            )

    async def upsert(self, account: PaperAccount) -> PaperAccount:
        """Insert or replace the account row keyed by account_id."""
        try:
            collection = PaperAccount.get_motor_collection()
            doc = account.model_dump(exclude={"id"})
            result = await collection.update_one(
                {"account_id": account.account_id},
                {"$set": doc},
                upsert=True,
            )
            if result.upserted_id:
                account.id = result.upserted_id  # type: ignore[assignment]
            return account
        except Exception as exc:
            logger.error(
                "Upsert PaperAccount failed for %s: %s", account.account_id, exc
            )
            raise DatabaseException(
                f"Failed to upsert PaperAccount {account.account_id}.",
                detail=str(exc),
            )
