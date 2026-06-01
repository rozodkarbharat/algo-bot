"""
BrokerSession repository — durable record of authenticated broker sessions.
"""

from typing import Optional

from app.core.exceptions import DatabaseException
from app.models.broker_session import BrokerSession, BrokerSessionStatus
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class BrokerSessionRepository(BaseRepository[BrokerSession]):
    document_model = BrokerSession

    async def get_by_broker(self, broker_name: str) -> Optional[BrokerSession]:
        try:
            return await BrokerSession.find_one({"broker_name": broker_name})
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch BrokerSession for {broker_name}.", detail=str(exc)
            )

    async def upsert(self, session: BrokerSession) -> BrokerSession:
        try:
            session.mark_updated()
            collection = BrokerSession.get_motor_collection()
            doc = session.model_dump(exclude={"id"})
            await collection.update_one(
                {"broker_name": session.broker_name},
                {"$set": doc},
                upsert=True,
            )
            return session
        except Exception as exc:
            logger.error("Upsert BrokerSession failed for %s: %s", session.broker_name, exc)
            raise DatabaseException(
                f"Failed to upsert BrokerSession {session.broker_name}.", detail=str(exc)
            )

    async def mark_status(
        self,
        broker_name: str,
        status: BrokerSessionStatus,
        error: Optional[str] = None,
    ) -> None:
        """Update only the session status / last_error fields."""
        try:
            from datetime import datetime, timezone
            update = {
                "session_status": status.value,
                "updated_at": datetime.now(timezone.utc),
            }
            if error is not None:
                update["last_error"] = error
                update["last_error_at"] = datetime.now(timezone.utc)
            collection = BrokerSession.get_motor_collection()
            await collection.update_one(
                {"broker_name": broker_name},
                {"$set": update},
                upsert=True,
            )
        except Exception as exc:
            raise DatabaseException(
                f"Failed to mark BrokerSession status for {broker_name}.",
                detail=str(exc),
            )
