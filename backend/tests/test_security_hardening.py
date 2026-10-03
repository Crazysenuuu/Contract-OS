"""Tests for security hardening (spec 1.22).

Refresh-token rotation, login rate limiting, and security headers.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import LoginAttempt, RefreshToken
from app.services.auth_security_service import (
    MAX_FAILED_ATTEMPTS_PER_EMAIL,
    create_refresh_token,
    revoke_all_for_user,
    validate_refresh_token,
)
from tests.conftest import activate_user


class TestRefreshTokens:
    async def test_create_and_validate(self, db_session: AsyncSession, test_user):
        row, raw = await create_refresh_token(
            db_session, user_id=test_user.id, session_id=None
        )
        await db_session.commit()

        # Only the hash is stored — never the raw token.
        assert raw != row.token_hash
        assert raw not in (await db_session.execute(
            select(RefreshToken.token_hash).where(RefreshToken.id == row.id)
        )).scalar_one()

        validated = await validate_refresh_token(db_session, raw)
        assert validated is not None
        user, token_row = validated
        assert user.id == test_user.id

    async def test_revoked_token_invalid(self, db_session: AsyncSession, test_user):
        row, raw = await create_refresh_token(
            db_session, user_id=test_user.id, session_id=None
        )
        await revoke_all_for_user(db_session, test_user.id, reason="logout")
        await db_session.commit()

        assert await validate_refresh_token(db_session, raw) is None


class TestRefreshApi:
    async def test_refresh_flow_and_reuse_detection(
        self, client, test_org, auth_headers, db_session
    ):
        # Register a fresh user so we get a real refresh token.
        reg = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "rotate@test.com",
                "name": "Rotate User",
                "password": "QuartzMeadow-Vellum7",
                "date_of_birth": "1990-01-01",
            },
        )
        assert reg.status_code in (200, 201)
        tokens = reg.json()
        assert "refresh_token" in tokens

        # First refresh works and returns a NEW refresh token.
        refresh = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": tokens["refresh_token"]},
        )
        assert refresh.status_code == 200
        new_refresh = refresh.json()["refresh_token"]
        assert new_refresh != tokens["refresh_token"]

        # Second refresh with the OLD token must fail (rotation).
        replay = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": tokens["refresh_token"]},
        )
        assert replay.status_code == 401

        # The NEW token still works (only the old one was rotated).
        ok = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": new_refresh},
        )
        assert ok.status_code == 200

    async def test_login_returns_refresh_token(self, client, db_session):
        reg = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "logintok@test.com",
                "name": "Login Tok",
                "password": "QuartzMeadow-Vellum7",
                "date_of_birth": "1990-01-01",
            },
        )
        assert reg.status_code in (200, 201)

        # Login refuses unverified accounts, so confirm the address first.
        await activate_user(db_session, "logintok@test.com")

        login = await client.post(
            "/api/v1/auth/login",
            json={"email": "logintok@test.com", "password": "QuartzMeadow-Vellum7"},
        )
        assert login.status_code == 200
        assert "refresh_token" in login.json()

    async def test_logout_revokes_refresh_tokens(self, client, test_user, auth_headers):
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": "test@example.com", "password": "TestPass123!"},
        )
        if login.status_code != 200:
            # Fixture user may not have a usable password path; rely on
            # register flow instead.
            reg = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": "logoutuser@test.com",
                    "name": "Logout User",
                    "password": "QuartzMeadow-Vellum7",
                    "date_of_birth": "1990-01-01",
                },
            )
            refresh_token = reg.json()["refresh_token"]
        else:
            refresh_token = login.json()["refresh_token"]

        logout = await client.post("/api/v1/auth/logout", headers=auth_headers)
        assert logout.status_code == 204

        refresh = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert refresh.status_code == 401


class TestRateLimiting:
    async def test_locked_after_repeated_failures(self, client, db_session: AsyncSession):
        # Register a user, then fail login repeatedly.
        await client.post(
            "/api/v1/auth/register",
            json={
                "email": "locked@test.com",
                "name": "Locked User",
                "password": "CorrectPass123!",
                "date_of_birth": "1990-01-01",
            },
        )

        for _ in range(MAX_FAILED_ATTEMPTS_PER_EMAIL):
            response = await client.post(
                "/api/v1/auth/login",
                json={"email": "locked@test.com", "password": "wrong"},
            )
            assert response.status_code == 401

        # Next attempt is rate-limited.
        limited = await client.post(
            "/api/v1/auth/login",
            json={"email": "locked@test.com", "password": "CorrectPass123!"},
        )
        assert limited.status_code == 429

    async def test_failed_attempts_recorded(self, db_session: AsyncSession, test_user):
        from app.services.auth_security_service import record_login_attempt

        await record_login_attempt(
            db_session,
            email=test_user.email,
            ip_address="10.0.0.1",
            success=False,
            reason="invalid_credentials",
        )
        await db_session.commit()

        result = await db_session.execute(
            select(LoginAttempt).where(LoginAttempt.email == test_user.email)
        )
        attempts = list(result.scalars().all())
        assert len(attempts) == 1
        assert attempts[0].success is False

    async def test_guest_review_routes_get_tight_rate_budget(self):
        """Guest /review/{token}* endpoints resolve to a tight per-IP budget.

        The token-based external-party surface carries no Authorization
        header, so each request is keyed by client IP (spec 3.20 brute-force
        resistance). It must not fall through to the 240/min default.
        """
        from app.core import rate_limit as rl

        for path in [
            "/review/TOKEN",
            "/review/TOKEN/verify-id/start",
            "/review/TOKEN/verify-id/kyc/complete",
            "/review/TOKEN/accept",
            "/review/TOKEN/sign",
        ]:
            path_class, (window, limit) = rl._rule_for(path)
            assert path_class == "/review/"
            assert limit < rl._DEFAULT_RULE[1], (
                f"{path} must be tighter than the default budget"
            )

        # The sliding window refuses once the per-identity budget is spent.
        path_class, (window, limit) = rl._rule_for("/review/TOKEN/verify-id/start")
        win = rl._Window()
        for _ in range(limit):
            allowed, _ = await win.hit("ip:10.0.0.1", window, limit)
            assert allowed is True
        blocked, retry_after = await win.hit("ip:10.0.0.1", window, limit)
        assert blocked is False
        assert retry_after > 0


class TestSecurityHeaders:
    async def test_security_headers_present(self, client, auth_headers):
        response = await client.get("/api/v1/auth/me", headers=auth_headers)
        assert response.status_code == 200
        assert response.headers.get("x-content-type-options") == "nosniff"
        assert response.headers.get("x-frame-options") == "DENY"
        assert response.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
        assert "default-src 'self'" in response.headers.get(
            "content-security-policy", ""
        )
        assert response.headers.get("cache-control") == "no-store"

    async def test_cors_middleware_still_works(self, client, auth_headers):
        response = await client.get(
            "/api/v1/auth/me",
            headers={**auth_headers, "Origin": "http://localhost:3000"},
        )
        assert response.status_code == 200