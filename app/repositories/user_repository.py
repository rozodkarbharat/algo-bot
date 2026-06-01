"""
User repository — CRUD operations for the users collection.

Follows the project's repository pattern: raw MongoDB filter dicts,
no direct Beanie field expressions (Beanie 2.x + Pydantic v2).
"""

from typing import Optional

from app.models.user import User
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class UserRepository(BaseRepository[User]):

    document_model = User

    async def get_by_username(self, username: str) -> Optional[User]:
        return await User.find_one({"username": username})

    async def get_by_email(self, email: str) -> Optional[User]:
        return await User.find_one({"email": email})

    async def get_active_users(self) -> list[User]:
        return await User.find({"is_active": True}).sort("username").to_list()

    async def count_users(self) -> int:
        return await User.find({}).count()

    async def username_exists(self, username: str) -> bool:
        return await User.find_one({"username": username}) is not None

    async def email_exists(self, email: str) -> bool:
        return await User.find_one({"email": email}) is not None
