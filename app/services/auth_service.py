"""
Authentication service.

Responsibilities:
  - Password hashing and verification (passlib/bcrypt)
  - JWT access token creation and validation (python-jose, HS256)
  - Stateful refresh token lifecycle:
      create → store jti in DB → rotate on /refresh → revoke on /logout
  - Account lockout: lock after LOGIN_MAX_ATTEMPTS failures for LOGIN_LOCKOUT_MINUTES
  - Token reuse detection: revoked-token reuse triggers full user logout
  - User seeding: create initial admin on first startup

Token design:
  Access tokens  — stateless JWT, 30-min TTL.  Validated on every protected request.
  Refresh tokens — JWT containing a `jti` claim that is persisted in `refresh_tokens`
                   collection.  Validation requires a DB round-trip, but this only
                   happens on the /refresh endpoint (infrequent).  Rotation revokes
                   the consumed jti and issues a fresh one atomically.
"""

import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config.settings import settings
from app.core.exceptions import AuthenticationException, AuthorizationException
from app.models.refresh_token import RefreshToken
from app.models.user import User, UserRole
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.user_repository import UserRepository
from app.schemas.auth import UserResponse
from app.utils.logger import get_logger

logger = get_logger(__name__)

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
_user_repo = UserRepository()
_rt_repo = RefreshTokenRepository()


# ── Password helpers ──────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


# ── Access token helpers (stateless) ─────────────────────────────────────────

def _make_jwt(payload: dict, expires_delta: timedelta) -> str:
    data = payload.copy()
    now = datetime.now(timezone.utc)
    data["exp"] = now + expires_delta
    data["iat"] = now
    return jwt.encode(data, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_access_token(user_id: str, username: str, role: str) -> str:
    return _make_jwt(
        {"sub": user_id, "username": username, "role": role, "type": "access"},
        timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )


def decode_access_token(token: str) -> dict:
    """Decode and validate an access token. Raises AuthenticationException on failure."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        if payload.get("type") != "access":
            raise AuthenticationException("Invalid token type.")
        return payload
    except JWTError as exc:
        raise AuthenticationException(f"Invalid or expired token: {exc}") from exc


# ── Refresh token helpers (DB-backed, stateful) ───────────────────────────────

async def _create_refresh_token_for_user(user_id: str) -> str:
    """Issue a new refresh token, persist its jti, return the signed JWT."""
    jti = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    rt = RefreshToken(jti=jti, user_id=user_id, expires_at=expires_at)
    await rt.insert()
    return _make_jwt(
        {"sub": user_id, "jti": jti, "type": "refresh"},
        timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )


async def _decode_and_consume_refresh_token(token: str) -> tuple[str, str]:
    """
    Decode refresh JWT, validate against DB, revoke it, return (user_id, jti).

    If a revoked token is re-presented (replay attack), all tokens for that user
    are revoked as a precaution (token family invalidation).
    Raises AuthenticationException on any failure.
    """
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except JWTError as exc:
        raise AuthenticationException(f"Invalid or expired refresh token: {exc}") from exc

    if payload.get("type") != "refresh":
        raise AuthenticationException("Invalid token type.")

    user_id: Optional[str] = payload.get("sub")
    jti: Optional[str] = payload.get("jti")
    if not user_id or not jti:
        raise AuthenticationException("Refresh token is missing required claims.")

    rt = await _rt_repo.get_by_jti(jti)
    if not rt:
        raise AuthenticationException("Refresh token not found.")

    if rt.revoked_at is not None:
        # Replay: token already consumed — revoke the whole family and force re-login
        logger.warning(
            "Revoked refresh token re-used — revoking all tokens for user_id=%s (jti=%s)",
            user_id, jti,
        )
        await _rt_repo.revoke_all_for_user(user_id)
        raise AuthenticationException(
            "Refresh token has already been used. Please log in again."
        )

    if not rt.is_valid:
        raise AuthenticationException("Refresh token has expired.")

    # Consume (revoke) the token — atomic rotation happens in the caller
    await _rt_repo.revoke(jti)
    return user_id, jti


# ── Account lockout helpers ───────────────────────────────────────────────────

async def _record_failed_login(user: User) -> None:
    user.failed_login_attempts += 1
    if user.failed_login_attempts >= settings.LOGIN_MAX_ATTEMPTS:
        user.locked_until = datetime.now(timezone.utc) + timedelta(
            minutes=settings.LOGIN_LOCKOUT_MINUTES
        )
        logger.warning(
            "Account '%s' locked after %d failed attempts. Unlock at %s UTC.",
            user.username, user.failed_login_attempts, user.locked_until.isoformat(),
        )
    user.updated_at = datetime.now(timezone.utc)
    await user.save()


async def _reset_lockout(user: User) -> None:
    if user.failed_login_attempts or user.locked_until:
        user.failed_login_attempts = 0
        user.locked_until = None
        user.updated_at = datetime.now(timezone.utc)
        await user.save()


# ── Core auth flows ───────────────────────────────────────────────────────────

async def authenticate_user(username: str, password: str) -> User:
    """Verify credentials, enforce lockout, return User on success."""
    user = await _user_repo.get_by_username(username)

    # Intentionally generic message to prevent username enumeration
    if not user:
        raise AuthenticationException("Invalid username or password.")

    if not user.is_active:
        raise AuthenticationException("Account is disabled.")

    # Lockout check
    if user.locked_until and user.locked_until > datetime.now(timezone.utc):
        remaining = max(
            1,
            int((user.locked_until - datetime.now(timezone.utc)).total_seconds() / 60) + 1,
        )
        raise AuthenticationException(
            f"Account is temporarily locked. Try again in {remaining} minute(s)."
        )

    if not verify_password(password, user.hashed_password):
        await _record_failed_login(user)
        raise AuthenticationException("Invalid username or password.")

    await _reset_lockout(user)
    return user


async def login(username: str, password: str) -> tuple[str, str, User]:
    """Full login flow. Returns (access_token, refresh_token, user)."""
    user = await authenticate_user(username, password)
    
    user.last_login = datetime.now(timezone.utc)
    await user.save()

    user_id = str(user.id)
    access = create_access_token(user_id, user.username, user.role)
    refresh = await _create_refresh_token_for_user(user_id)

    logger.info("User '%s' (%s) logged in.", user.username, user.role)
    return access, refresh, user


async def refresh_access_token(refresh_token: str) -> tuple[str, str, User]:
    """
    Validate and rotate a refresh token.

    Returns (new_access_token, new_refresh_token, user).
    The old refresh token is revoked; a fresh one is issued atomically.
    """
    user_id, _consumed_jti = await _decode_and_consume_refresh_token(refresh_token)

    user = await User.find_one({"_id": user_id})
    if not user or not user.is_active:
        raise AuthenticationException("User not found or disabled.")

    new_access = create_access_token(str(user.id), user.username, user.role)
    new_refresh = await _create_refresh_token_for_user(str(user.id))
    return new_access, new_refresh, user


async def logout(refresh_token: Optional[str], user: Optional[User] = None) -> None:
    """
    Revoke the supplied refresh token.

    When refresh_token is None (client-side-only logout), only the audit log
    entry is written — the old token will expire naturally.
    """
    if not refresh_token:
        return

    try:
        payload = jwt.decode(
            refresh_token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
        jti: Optional[str] = payload.get("jti")
        if jti:
            await _rt_repo.revoke(jti)
            logger.info(
                "Refresh token revoked on logout (jti=%s, user=%s).",
                jti,
                user.username if user else "unknown",
            )
    except JWTError:
        # Malformed token — nothing to revoke; log and ignore
        logger.warning("Logout received a malformed refresh token — skipping revocation.")


async def get_user_from_token(token: str) -> User:
    """Decode access token and return the live User document."""
    payload = decode_access_token(token)
    user_id: Optional[str] = payload.get("sub")
    if not user_id:
        raise AuthenticationException("Token missing subject.")

    user = await User.find_one({"_id": user_id})
    if not user or not user.is_active:
        raise AuthenticationException("User not found or disabled.")
    return user


# ── User management ───────────────────────────────────────────────────────────

async def create_user(
    username: str,
    email: str,
    password: str,
    role: UserRole = UserRole.VIEWER,
) -> User:
    if await _user_repo.username_exists(username):
        from app.core.exceptions import ValidationException
        raise ValidationException(f"Username '{username}' is already taken.")
    if await _user_repo.email_exists(email):
        from app.core.exceptions import ValidationException
        raise ValidationException(f"Email '{email}' is already registered.")

    user = User(
        username=username,
        email=email,
        hashed_password=hash_password(password),
        role=role,
    )
    await user.insert()
    logger.info("Created user '%s' with role %s.", username, role)
    return user


async def change_password(user: User, current_password: str, new_password: str) -> None:
    if not verify_password(current_password, user.hashed_password):
        raise AuthenticationException("Current password is incorrect.")
    user.hashed_password = hash_password(new_password)
    user.password_changed_at = datetime.now(timezone.utc)
    user.updated_at = datetime.now(timezone.utc)
    await user.save()
    # Revoke all existing refresh tokens — forces re-login on all devices
    await _rt_repo.revoke_all_for_user(str(user.id))
    logger.info("Password changed for user '%s'. All refresh tokens revoked.", user.username)


# ── Seed admin ────────────────────────────────────────────────────────────────

async def seed_admin_if_missing() -> None:
    """Create the initial admin user if no users exist in the database."""
    count = await _user_repo.count_users()
    if count > 0:
        return

    logger.warning(
        "No users found — creating initial admin user '%s'.",
        settings.INITIAL_ADMIN_USERNAME,
    )
    await create_user(
        username=settings.INITIAL_ADMIN_USERNAME,
        email=settings.INITIAL_ADMIN_EMAIL,
        password=settings.INITIAL_ADMIN_PASSWORD,
        role=UserRole.ADMIN,
    )
    logger.warning(
        "Initial admin created. CHANGE THE PASSWORD immediately: "
        "POST /api/v1/auth/change-password",
    )


# ── User → response schema ────────────────────────────────────────────────────

def user_to_response(user: User) -> UserResponse:
    return UserResponse(
        id=str(user.id),
        username=user.username,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
        last_login=user.last_login,
        created_at=user.created_at,
    )
