"""
Tests for authentication, authorization, account lockout, token rotation,
and audit logging.

Design:
  - All MongoDB I/O is avoided by injecting mock repositories and using
    model_construct() to bypass Beanie initialization.
  - JWT encoding/decoding uses the same settings singleton — tests that
    need predictable tokens override settings fields via monkeypatch.
  - Async tests use pytest-asyncio with asyncio_mode="auto" (see conftest).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from jose import jwt

from app.config.settings import settings
from app.core.exceptions import AuthenticationException, AuthorizationException
from app.models.refresh_token import RefreshToken
from app.models.user import User, UserRole
from app.services import auth_service as _auth


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_user(
    *,
    username: str = "testuser",
    role: UserRole = UserRole.TRADER,
    is_active: bool = True,
    hashed_password: str | None = None,
    failed_login_attempts: int = 0,
    locked_until: Optional[datetime] = None,
) -> User:
    return User.model_construct(
        id="64f000000000000000000001",
        username=username,
        email=f"{username}@test.local",
        hashed_password=hashed_password or _auth.hash_password("password123"),
        role=role,
        is_active=is_active,
        last_login=None,
        password_changed_at=None,
        failed_login_attempts=failed_login_attempts,
        locked_until=locked_until,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def _make_refresh_token_doc(
    jti: str,
    user_id: str,
    *,
    revoked_at: Optional[datetime] = None,
    expires_delta: timedelta = timedelta(days=30),
) -> RefreshToken:
    return RefreshToken.model_construct(
        jti=jti,
        user_id=user_id,
        expires_at=datetime.now(timezone.utc) + expires_delta,
        revoked_at=revoked_at,
        created_at=datetime.now(timezone.utc),
    )


def _decode_jwt(token: str) -> dict:
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])


# ── Password helpers ──────────────────────────────────────────────────────────

class TestPasswordHelpers:
    def test_hash_and_verify_roundtrip(self) -> None:
        plain = "s3cr3t!"
        hashed = _auth.hash_password(plain)
        assert hashed != plain
        assert _auth.verify_password(plain, hashed)

    def test_wrong_password_fails(self) -> None:
        hashed = _auth.hash_password("correct")
        assert not _auth.verify_password("wrong", hashed)


# ── Access token ──────────────────────────────────────────────────────────────

class TestAccessToken:
    def test_create_and_decode(self) -> None:
        token = _auth.create_access_token("uid1", "alice", "admin")
        payload = _decode_jwt(token)
        assert payload["sub"] == "uid1"
        assert payload["username"] == "alice"
        assert payload["role"] == "admin"
        assert payload["type"] == "access"

    def test_decode_rejects_wrong_type(self) -> None:
        token = _auth.create_access_token("uid1", "alice", "admin")
        # Forge a refresh-type token using the same payload
        forged = jwt.encode(
            {**_decode_jwt(token), "type": "refresh"},
            settings.JWT_SECRET,
            algorithm=settings.JWT_ALGORITHM,
        )
        with pytest.raises(AuthenticationException, match="Invalid token type"):
            _auth.decode_access_token(forged)

    def test_decode_rejects_tampered_signature(self) -> None:
        token = _auth.create_access_token("uid1", "alice", "admin")
        tampered = token[:-5] + "XXXXX"
        with pytest.raises(AuthenticationException):
            _auth.decode_access_token(tampered)


# ── Login flow ────────────────────────────────────────────────────────────────

class TestLogin:
    @pytest.mark.asyncio
    async def test_successful_login(self) -> None:
        user = _make_user(username="alice")
        user.save = AsyncMock()

        with (
            patch.object(_auth._user_repo, "get_by_username", return_value=user),
            patch(
                "app.services.auth_service._create_refresh_token_for_user",
                new_callable=AsyncMock,
                return_value="mock_refresh_jwt",
            ),
        ):
            access, refresh, returned_user = await _auth.login("alice", "password123")

        assert returned_user.username == "alice"
        assert refresh == "mock_refresh_jwt"
        payload = _decode_jwt(access)
        assert payload["type"] == "access"
        assert payload["username"] == "alice"

    @pytest.mark.asyncio
    async def test_wrong_password_raises(self) -> None:
        user = _make_user(username="alice")
        user.save = AsyncMock()

        with patch.object(_auth._user_repo, "get_by_username", return_value=user):
            with pytest.raises(AuthenticationException, match="Invalid username or password"):
                await _auth.login("alice", "wrongpassword")

    @pytest.mark.asyncio
    async def test_unknown_user_raises(self) -> None:
        with patch.object(_auth._user_repo, "get_by_username", return_value=None):
            with pytest.raises(AuthenticationException, match="Invalid username or password"):
                await _auth.login("ghost", "anything")

    @pytest.mark.asyncio
    async def test_inactive_user_raises(self) -> None:
        user = _make_user(is_active=False)
        with patch.object(_auth._user_repo, "get_by_username", return_value=user):
            with pytest.raises(AuthenticationException, match="disabled"):
                await _auth.login("testuser", "password123")


# ── Account lockout ───────────────────────────────────────────────────────────

class TestAccountLockout:
    @pytest.mark.asyncio
    async def test_lockout_applied_after_max_attempts(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "LOGIN_MAX_ATTEMPTS", 3)
        monkeypatch.setattr(settings, "LOGIN_LOCKOUT_MINUTES", 30)

        user = _make_user(failed_login_attempts=2)  # one away from lockout
        user.save = AsyncMock()

        with patch.object(_auth._user_repo, "get_by_username", return_value=user):
            with pytest.raises(AuthenticationException, match="Invalid username or password"):
                await _auth.login("testuser", "wrongpassword")

        assert user.failed_login_attempts == 3
        assert user.locked_until is not None
        assert user.locked_until > datetime.now(timezone.utc)

    @pytest.mark.asyncio
    async def test_locked_account_raises_lockout_error(self) -> None:
        locked_until = datetime.now(timezone.utc) + timedelta(minutes=25)
        user = _make_user(
            failed_login_attempts=5,
            locked_until=locked_until,
        )

        with patch.object(_auth._user_repo, "get_by_username", return_value=user):
            with pytest.raises(AuthenticationException, match="temporarily locked"):
                await _auth.login("testuser", "password123")

    @pytest.mark.asyncio
    async def test_expired_lockout_allows_login(self) -> None:
        expired_lockout = datetime.now(timezone.utc) - timedelta(minutes=1)
        user = _make_user(
            failed_login_attempts=5,
            locked_until=expired_lockout,
        )
        user.save = AsyncMock()

        with (
            patch.object(_auth._user_repo, "get_by_username", return_value=user),
            patch(
                "app.services.auth_service._create_refresh_token_for_user",
                new_callable=AsyncMock,
                return_value="mock_refresh",
            ),
        ):
            # Should NOT raise — lockout has expired
            access, _, _ = await _auth.login("testuser", "password123")
        assert _decode_jwt(access)["type"] == "access"
        # Lockout should be cleared on successful login
        assert user.failed_login_attempts == 0
        assert user.locked_until is None

    @pytest.mark.asyncio
    async def test_successful_login_resets_failed_counter(self) -> None:
        user = _make_user(failed_login_attempts=2)
        user.save = AsyncMock()

        with (
            patch.object(_auth._user_repo, "get_by_username", return_value=user),
            patch(
                "app.services.auth_service._create_refresh_token_for_user",
                new_callable=AsyncMock,
                return_value="r",
            ),
        ):
            await _auth.login("testuser", "password123")

        assert user.failed_login_attempts == 0


# ── Refresh token rotation ────────────────────────────────────────────────────

class TestTokenRefresh:
    @pytest.mark.asyncio
    async def test_valid_refresh_rotates_tokens(self) -> None:
        user = _make_user(username="alice")
        user.save = AsyncMock()

        jti = str(uuid.uuid4())
        # Create a real signed refresh JWT with the jti
        refresh_jwt = jwt.encode(
            {
                "sub": str(user.id),
                "jti": jti,
                "type": "refresh",
                "exp": datetime.now(timezone.utc) + timedelta(days=30),
                "iat": datetime.now(timezone.utc),
            },
            settings.JWT_SECRET,
            algorithm=settings.JWT_ALGORITHM,
        )
        rt_doc = _make_refresh_token_doc(jti, str(user.id))
        rt_doc.save = AsyncMock()

        with (
            patch.object(_auth._rt_repo, "get_by_jti", return_value=rt_doc),
            patch.object(_auth._rt_repo, "revoke", new_callable=AsyncMock),
            patch("app.services.auth_service.User.find_one", new_callable=AsyncMock, return_value=user),
            patch(
                "app.services.auth_service._create_refresh_token_for_user",
                new_callable=AsyncMock,
                return_value="new_refresh_jwt",
            ),
        ):
            new_access, new_refresh, returned_user = await _auth.refresh_access_token(refresh_jwt)

        assert new_refresh == "new_refresh_jwt"
        assert _decode_jwt(new_access)["type"] == "access"

    @pytest.mark.asyncio
    async def test_revoked_refresh_token_raises(self) -> None:
        user_id = "uid1"
        jti = str(uuid.uuid4())
        refresh_jwt = jwt.encode(
            {
                "sub": user_id,
                "jti": jti,
                "type": "refresh",
                "exp": datetime.now(timezone.utc) + timedelta(days=30),
                "iat": datetime.now(timezone.utc),
            },
            settings.JWT_SECRET,
            algorithm=settings.JWT_ALGORITHM,
        )
        # Already-revoked token doc
        rt_doc = _make_refresh_token_doc(
            jti, user_id, revoked_at=datetime.now(timezone.utc) - timedelta(hours=1)
        )

        with (
            patch.object(_auth._rt_repo, "get_by_jti", return_value=rt_doc),
            patch.object(_auth._rt_repo, "revoke_all_for_user", new_callable=AsyncMock),
        ):
            with pytest.raises(AuthenticationException, match="already been used"):
                await _auth.refresh_access_token(refresh_jwt)

    @pytest.mark.asyncio
    async def test_nonexistent_refresh_token_raises(self) -> None:
        user_id = "uid1"
        jti = str(uuid.uuid4())
        refresh_jwt = jwt.encode(
            {
                "sub": user_id,
                "jti": jti,
                "type": "refresh",
                "exp": datetime.now(timezone.utc) + timedelta(days=30),
                "iat": datetime.now(timezone.utc),
            },
            settings.JWT_SECRET,
            algorithm=settings.JWT_ALGORITHM,
        )

        with patch.object(_auth._rt_repo, "get_by_jti", return_value=None):
            with pytest.raises(AuthenticationException, match="not found"):
                await _auth.refresh_access_token(refresh_jwt)


# ── Logout ────────────────────────────────────────────────────────────────────

class TestLogout:
    @pytest.mark.asyncio
    async def test_logout_revokes_refresh_token(self) -> None:
        user = _make_user()
        jti = str(uuid.uuid4())
        refresh_jwt = jwt.encode(
            {
                "sub": str(user.id),
                "jti": jti,
                "type": "refresh",
                "exp": datetime.now(timezone.utc) + timedelta(days=30),
                "iat": datetime.now(timezone.utc),
            },
            settings.JWT_SECRET,
            algorithm=settings.JWT_ALGORITHM,
        )

        with patch.object(_auth._rt_repo, "revoke", new_callable=AsyncMock) as mock_revoke:
            await _auth.logout(refresh_jwt, user)
        mock_revoke.assert_called_once_with(jti)

    @pytest.mark.asyncio
    async def test_logout_without_token_is_noop(self) -> None:
        user = _make_user()
        with patch.object(_auth._rt_repo, "revoke", new_callable=AsyncMock) as mock_revoke:
            await _auth.logout(None, user)
        mock_revoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_logout_with_malformed_token_is_safe(self) -> None:
        user = _make_user()
        # Should not raise
        await _auth.logout("this.is.garbage", user)


# ── Permission checks ─────────────────────────────────────────────────────────

class TestPermissionChecks:
    """
    Tests for the auth middleware dependency functions.
    We patch settings.AUTH_REQUIRED=True and test role enforcement.
    """

    @pytest.mark.asyncio
    async def test_require_admin_allows_admin(self) -> None:
        from app.middleware.auth_middleware import require_admin

        admin = _make_user(role=UserRole.ADMIN)
        result = await require_admin(current_user=admin)
        assert result.role == UserRole.ADMIN

    @pytest.mark.asyncio
    async def test_require_admin_rejects_trader(self) -> None:
        from app.middleware.auth_middleware import require_admin

        trader = _make_user(role=UserRole.TRADER)
        with pytest.raises(AuthorizationException, match="Admin role required"):
            await require_admin(current_user=trader)

    @pytest.mark.asyncio
    async def test_require_admin_rejects_viewer(self) -> None:
        from app.middleware.auth_middleware import require_admin

        viewer = _make_user(role=UserRole.VIEWER)
        with pytest.raises(AuthorizationException):
            await require_admin(current_user=viewer)

    @pytest.mark.asyncio
    async def test_require_trader_allows_trader(self) -> None:
        from app.middleware.auth_middleware import require_trader

        trader = _make_user(role=UserRole.TRADER)
        result = await require_trader(current_user=trader)
        assert result.role == UserRole.TRADER

    @pytest.mark.asyncio
    async def test_require_trader_allows_admin(self) -> None:
        from app.middleware.auth_middleware import require_trader

        admin = _make_user(role=UserRole.ADMIN)
        result = await require_trader(current_user=admin)
        assert result.role == UserRole.ADMIN

    @pytest.mark.asyncio
    async def test_require_trader_rejects_viewer(self) -> None:
        from app.middleware.auth_middleware import require_trader

        viewer = _make_user(role=UserRole.VIEWER)
        with pytest.raises(AuthorizationException, match="Trader or Admin role required"):
            await require_trader(current_user=viewer)


# ── Audit logging ─────────────────────────────────────────────────────────────

class TestAuditLogging:
    @pytest.mark.asyncio
    async def test_audit_log_is_written(self) -> None:
        from app.services.audit_service import AuditService

        captured: list[dict] = []

        class _FakeAuditLog:
            def __init__(self, **kwargs):
                captured.append(kwargs)

            async def insert(self):
                pass

        service = AuditService()
        user = _make_user(username="alice", role=UserRole.ADMIN)

        with patch("app.services.audit_service.AuditLog", _FakeAuditLog):
            await service.log(
                action="login",
                resource="auth",
                user=user,
                detail={"ip": "127.0.0.1"},
            )

        assert len(captured) == 1
        assert captured[0]["action"] == "login"
        assert captured[0]["resource"] == "auth"
        assert captured[0]["username"] == "alice"

    @pytest.mark.asyncio
    async def test_audit_log_never_raises(self) -> None:
        from app.services.audit_service import AuditService

        service = AuditService()

        class _ExplodingLog:
            def __init__(self, **kwargs): pass
            async def insert(self): raise RuntimeError("DB exploded")

        with patch("app.services.audit_service.AuditLog", _ExplodingLog):
            # Must NOT raise — audit writes are best-effort
            await service.log(action="test", resource="test")

    @pytest.mark.asyncio
    async def test_audit_log_captures_request_ip(self) -> None:
        from app.services.audit_service import AuditService
        from unittest.mock import MagicMock

        captured: list[dict] = []

        class _FakeAuditLog:
            def __init__(self, **kwargs): captured.append(kwargs)
            async def insert(self): pass

        service = AuditService()
        mock_request = MagicMock()
        mock_request.headers.get.side_effect = lambda k: "192.168.1.1" if k == "x-forwarded-for" else None
        mock_request.client = MagicMock(host="10.0.0.1")

        with patch("app.services.audit_service.AuditLog", _FakeAuditLog):
            await service.log(action="login", resource="auth", request=mock_request)

        assert captured[0]["ip_address"] == "192.168.1.1"


# ── Change password ───────────────────────────────────────────────────────────

class TestChangePassword:
    @pytest.mark.asyncio
    async def test_change_password_revokes_all_refresh_tokens(self) -> None:
        user = _make_user(username="alice")
        user.save = AsyncMock()

        with patch.object(
            _auth._rt_repo, "revoke_all_for_user", new_callable=AsyncMock
        ) as mock_revoke_all:
            await _auth.change_password(user, "password123", "NewSecure!456")

        mock_revoke_all.assert_called_once_with(str(user.id))
        assert _auth.verify_password("NewSecure!456", user.hashed_password)

    @pytest.mark.asyncio
    async def test_change_password_wrong_current_raises(self) -> None:
        user = _make_user()
        with pytest.raises(AuthenticationException, match="Current password is incorrect"):
            await _auth.change_password(user, "wrong_current", "NewSecure!456")
