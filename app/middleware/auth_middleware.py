"""
Authentication middleware — FastAPI dependencies for JWT validation.

Design:
  - Uses FastAPI's Depends() pattern (not ASGI middleware) so individual
    routes can be selectively protected and the dependency return value
    (the User object) is available in route functions.
  - When settings.AUTH_REQUIRED is False (default in dev), all dependencies
    return a synthetic admin user. Existing tests continue to pass.
  - Three dependency tiers:
      get_current_user   — any authenticated user (token required in prod)
      require_trader     — role TRADER or ADMIN
      require_admin      — role ADMIN only

Usage in a route:
    @router.post("/sensitive-action")
    async def action(current_user: User = Depends(require_trader)):
        ...

Usage at router level (all routes in router require auth):
    router = APIRouter(dependencies=[Depends(get_current_user)])
"""

from typing import Optional

from fastapi import Depends, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config.settings import settings
from app.core.exceptions import AuthenticationException, AuthorizationException
from app.models.user import User, UserRole
from app.services import auth_service as _auth
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Optional bearer — returns None if header is absent (needed when AUTH_REQUIRED=False)
_optional_bearer = HTTPBearer(auto_error=False)
_required_bearer = HTTPBearer(auto_error=True)

# Synthetic dev user returned when AUTH_REQUIRED=False — built lazily after Beanie init
_DEV_USER: Optional[User] = None


def _get_dev_user() -> User:
    global _DEV_USER
    if _DEV_USER is None:
        _DEV_USER = User(
            username="dev_admin",
            email="dev@tradingbot.local",
            hashed_password="",
            role=UserRole.ADMIN,
            is_active=True,
        )
    return _DEV_USER


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(_optional_bearer),
) -> User:
    """
    Extract and validate the bearer token from the Authorization header.

    In development (AUTH_REQUIRED=False): returns a synthetic admin user so
    existing tests and local development work without supplying tokens.

    In production (AUTH_REQUIRED=True): validates the JWT and loads the live
    User document; raises 401 if the token is missing or invalid.
    """
    if not settings.AUTH_REQUIRED:
        return _get_dev_user()

    if not credentials:
        raise AuthenticationException("Authorization header is required.")

    return await _auth.get_user_from_token(credentials.credentials)


async def require_trader(current_user: User = Depends(get_current_user)) -> User:
    """Require TRADER or ADMIN role."""
    if current_user.role not in (UserRole.TRADER, UserRole.ADMIN):
        raise AuthorizationException(
            "Trader or Admin role required for this action."
        )
    return current_user


async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Require ADMIN role."""
    if current_user.role != UserRole.ADMIN:
        raise AuthorizationException("Admin role required for this action.")
    return current_user


async def get_current_user_or_none(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(_optional_bearer),
) -> Optional[User]:
    """
    Like get_current_user but returns None instead of raising for missing/invalid tokens.
    Used by audit logging where the user may be anonymous.
    """
    if not settings.AUTH_REQUIRED:
        return _get_dev_user()
    if not credentials:
        return None
    try:
        return await _auth.get_user_from_token(credentials.credentials)
    except AuthenticationException:
        return None
