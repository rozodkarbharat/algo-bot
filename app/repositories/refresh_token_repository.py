"""
RefreshToken repository — CRUD for the refresh_tokens collection.
"""

from datetime import datetime, timezone
from typing import Optional

from app.models.refresh_token import RefreshToken
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class RefreshTokenRepository(BaseRepository[RefreshToken]):

    document_model = RefreshToken

    async def get_by_jti(self, jti: str) -> Optional[RefreshToken]:
        return await RefreshToken.find_one({"jti": jti})

    async def revoke(self, jti: str) -> bool:
        token = await self.get_by_jti(jti)
        if not token:
            return False
        token.revoked_at = datetime.now(timezone.utc)
        await token.save()
        return True

    async def revoke_all_for_user(self, user_id: str) -> int:
        """Revoke all active (non-revoked) refresh tokens for a user.

        Called on password change and on token reuse detection.
        Returns the count of revoked tokens.
        """
        now = datetime.now(timezone.utc)
        active = await RefreshToken.find(
            {"user_id": user_id, "revoked_at": None}
        ).to_list()
        for token in active:
            token.revoked_at = now
            await token.save()
        if active:
            logger.warning(
                "Revoked %d refresh token(s) for user_id=%s", len(active), user_id
            )
        return len(active)

    async def count_active_for_user(self, user_id: str) -> int:
        return await RefreshToken.find(
            {"user_id": user_id, "revoked_at": None}
        ).count()
