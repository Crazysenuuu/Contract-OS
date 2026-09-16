"""OIDC authorization-code flow tests (spec 1.21.8 / 2.15)."""

import base64
import json
import secrets
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk, jwt as jose_jwt
from sqlalchemy.ext.asyncio import AsyncSession
from httpx import AsyncClient, ASGITransport

from app.core.config import get_settings_lazy
from app.core.security import create_access_token, hash_password
from app.models.organization import Organization
from app.models.rbac import OrganizationMember, Role
from app.models.sso import SSOConnection
from app.services import oidc_service


settings = get_settings_lazy()

# --- Test RSA key-pair -----------------------------------------------------


@pytest.fixture
def rsa_private_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def rsa_public_jwk_dict(rsa_private_key):
    pn = rsa_private_key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "n": base64.urlsafe_b64encode(
            pn.n.to_bytes((pn.n.bit_length() + 7) // 8, "big")
        )
        .rstrip(b"=")
        .decode(),
        "e": "AQAB",
        "kid": "test-kid-001",
        "alg": "RS256",
        "use": "sig",
    }


@pytest.fixture
def rsa_private_pem(rsa_private_key):
    from cryptography.hazmat.primitives import serialization

    return rsa_private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


# --- State token round-trip ------------------------------------------------


def test_state_token_roundtrip():
    conn_id = uuid.uuid4()
    nonce = secrets.token_urlsafe(24)
    state = oidc_service.create_state_token(connection_id=conn_id, nonce=nonce)
    decoded_conn, decoded_nonce = oidc_service.decode_state_token(state)
    assert decoded_conn == conn_id
    assert decoded_nonce == nonce


def test_state_token_rejects_tampered():
    conn_id = uuid.uuid4()
    state = oidc_service.create_state_token(connection_id=conn_id, nonce="aaa")
    parts = state.split(".")
    parts[0] = parts[0][::-1]  # tamper with signature
    tampered = ".".join(parts)
    with pytest.raises(oidc_service.TokenValidationError):
        oidc_service.decode_state_token(tampered)


def test_state_token_rejects_wrong_signature():
    conn_id = uuid.uuid4()
    state = oidc_service.create_state_token(connection_id=conn_id, nonce="aaa")
    with patch(
        "app.services.oidc_service.settings.jwt_secret_key",
        get_settings_lazy().jwt_secret_key,
    ):
        with pytest.raises(oidc_service.TokenValidationError):
            oidc_service.decode_state_token(state + "extra")


# --- verify_id_token -------------------------------------------------------


def _make_id_token(rsa_private_pem, kid, issuer, audience, nonce, claims_extra=None):
    payload = {"iss": issuer, "aud": audience, "nonce": nonce, "sub": "idp-user-1"}
    if claims_extra:
        payload.update(claims_extra)
    return jose_jwt.encode(payload, rsa_private_pem, algorithm="RS256", headers={"kid": kid})


def test_verify_id_token_valid(rsa_private_pem, rsa_public_jwk_dict):
    issuer = "https://accounts.example.com"
    audience = "my-client-id"
    nonce = secrets.token_urlsafe(24)
    token = _make_id_token(rsa_private_pem, "test-kid-001", issuer, audience, nonce)
    claims = oidc_service.verify_id_token(
        id_token=token,
        jwks={"keys": [rsa_public_jwk_dict]},
        issuer=issuer,
        client_id=audience,
        nonce=nonce,
    )
    assert claims["iss"] == issuer
    assert claims["aud"] == audience
    assert claims["nonce"] == nonce
    assert claims["sub"] == "idp-user-1"


def test_verify_id_token_wrong_issuer(rsa_private_pem, rsa_public_jwk_dict):
    nonce = secrets.token_urlsafe(24)
    token = _make_id_token(
        rsa_private_pem, "test-kid-001", "https://bad.com", "aud", nonce
    )
    with pytest.raises(oidc_service.TokenValidationError, match="ID token verification failed"):
        oidc_service.verify_id_token(
            id_token=token,
            jwks={"keys": [rsa_public_jwk_dict]},
            issuer="https://accounts.example.com",
            client_id="aud",
            nonce=nonce,
        )


def test_verify_id_token_wrong_audience(rsa_private_pem, rsa_public_jwk_dict):
    issuer = "https://accounts.example.com"
    nonce = secrets.token_urlsafe(24)
    token = _make_id_token(rsa_private_pem, "test-kid-001", issuer, "wrong-aud", nonce)
    with pytest.raises(oidc_service.TokenValidationError, match="ID token verification failed"):
        oidc_service.verify_id_token(
            id_token=token,
            jwks={"keys": [rsa_public_jwk_dict]},
            issuer=issuer,
            client_id="correct-aud",
            nonce=nonce,
        )


def test_verify_id_token_wrong_nonce(rsa_private_pem, rsa_public_jwk_dict):
    issuer = "https://accounts.example.com"
    audience = "my-client"
    nonce = secrets.token_urlsafe(24)
    token = _make_id_token(rsa_private_pem, "test-kid-001", issuer, audience, nonce)
    with pytest.raises(oidc_service.TokenValidationError, match="nonce mismatch"):
        oidc_service.verify_id_token(
            id_token=token,
            jwks={"keys": [rsa_public_jwk_dict]},
            issuer=issuer,
            client_id=audience,
            nonce="wrong-nonce",
        )


def test_verify_id_token_wrong_signing_key():
    """Token signed with a different key is rejected."""
    bad_priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    from cryptography.hazmat.primitives import serialization

    bad_pem = bad_priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    good_jwk = {
        "kty": "RSA",
        "n": "abc",
        "e": "AQAB",
        "kid": "test-kid-001",
        "alg": "RS256",
    }
    issuer = "https://accounts.example.com"
    nonce = secrets.token_urlsafe(24)
    token = jose_jwt.encode(
        {"iss": issuer, "aud": "aud", "nonce": nonce, "sub": "u1"},
        bad_pem,
        algorithm="RS256",
        headers={"kid": "test-kid-001"},
    )
    with pytest.raises(oidc_service.TokenValidationError):
        oidc_service.verify_id_token(
            id_token=token,
            jwks={"keys": [good_jwk]},
            issuer=issuer,
            client_id="aud",
            nonce=nonce,
        )


# --- Full authorize + callback API round-trip -----------------------------


@pytest.mark.asyncio
async def test_oidc_callback_provisions_user(
    db_session: AsyncSession,
    test_user,
    test_org: Organization,
    rsa_private_pem,
    rsa_public_jwk_dict,
    engine,
):
    """End-to-end: create an SSO connection, mock IdP responses, hit the
    /sso/oidc/callback endpoint and verify a new user + tokens are issued.
    """
    from app.main import app
    from app.core.database import get_db
    from app.api.v1.sso import router as _  # ensure router loaded

    # Create an OIDC connection in DB
    connection = SSOConnection(
        organization_id=test_org.id,
        protocol="oidc",
        name="Test IdP",
        issuer="https://accounts.example.com",
        client_id="test-client-id",
        client_secret_ref="TEST_OIDC_SECRET",
        default_role="member",
        enforce_sso=False,
    )
    db_session.add(connection)
    await db_session.flush()
    connection_id = connection.id

    # IdP endpoints
    discovery_doc = {
        "issuer": "https://accounts.example.com",
        "authorization_endpoint": "https://accounts.example.com/auth",
        "token_endpoint": "https://accounts.example.com/token",
        "jwks_uri": "https://accounts.example.com/.well-known/jwks",
    }

    id_token = _make_id_token(
        rsa_private_pem,
        "test-kid-001",
        "https://accounts.example.com",
        "test-client-id",
        "test-nonce-123",
        claims_extra={"email": "alice@corp.example.com", "name": "Alice Smith"},
    )
    token_response = {
        "id_token": id_token,
        "access_token": "idp-access-tok",
        "token_type": "Bearer",
    }

    # State token for the callback
    state_nonce = "test-nonce-123"
    state_token = oidc_service.create_state_token(
        connection_id=connection_id, nonce=state_nonce
    )

    # Override DB dependency for API test client
    from sqlalchemy.ext.asyncio import async_sessionmaker

    sf = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with sf() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        with patch.object(
            oidc_service, "discover_config", AsyncMock(return_value=discovery_doc)
        ), patch.object(
            oidc_service,
            "fetch_jwks",
            AsyncMock(return_value={"keys": [rsa_public_jwk_dict]}),
        ), patch.object(
            oidc_service, "exchange_code", AsyncMock(return_value=token_response)
        ):
            callback_resp = await ac.get(
                "/api/v1/sso/oidc/callback",
                params={"code": "auth-code-xyz", "state": state_token, "redirect_uri": "http://localhost:3000/api/v1/sso/oidc/callback"},
            )
            assert callback_resp.status_code == 200, callback_resp.text
            body = callback_resp.json()
            assert "access_token" in body
            assert "refresh_token" in body
            assert body["email"] == "alice@corp.example.com"

            # Verify the new user exists in DB
            from sqlalchemy import select
            from app.models.user import User

            new_user = (
                await db_session.execute(
                    select(User).where(User.email == "alice@corp.example.com")
                )
            ).scalar_one_or_none()
            assert new_user is not None
            assert new_user.status == "active"
            assert new_user.password_hash == "!sso-no-password"

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_oidc_authorize_returns_authorization_url(
    db_session: AsyncSession,
    test_user,
    test_org: Organization,
    engine,
):
    """GET /sso/oidc/{id}/authorize builds an IdP auth URL for the caller's
    connection (tenant-scoped) with code + nonce + state."""
    from app.main import app
    from app.core.database import get_db
    from sqlalchemy.ext.asyncio import async_sessionmaker

    connection = SSOConnection(
        organization_id=test_org.id,
        protocol="oidc",
        name="Test IdP",
        issuer="https://accounts.example.com",
        client_id="test-client-id",
        client_secret_ref="TEST_OIDC_SECRET",
        default_role="member",
        enforce_sso=False,
    )
    db_session.add(connection)
    await db_session.flush()
    connection_id = connection.id

    sf = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with sf() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db

    token = create_access_token(user_id=test_user.id, organization_id=test_org.id)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        with patch.object(
            oidc_service,
            "discover_config",
            AsyncMock(
                return_value={
                    "issuer": "https://accounts.example.com",
                    "authorization_endpoint": "https://accounts.example.com/auth",
                    "token_endpoint": "https://accounts.example.com/token",
                    "jwks_uri": "https://accounts.example.com/.well-known/jwks",
                }
            ),
        ):
            resp = await ac.get(
                f"/api/v1/sso/oidc/{connection_id}/authorize",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            url = body["authorization_url"]
            assert url.startswith("https://accounts.example.com/auth")
            assert "response_type=code" in url
            assert "client_id=test-client-id" in url
            assert "state=" in url
            assert "nonce=" in url
            # state decodes back to this connection
            from urllib.parse import parse_qs, urlsplit

            params = parse_qs(urlsplit(url).query)
            decoded_conn, _ = oidc_service.decode_state_token(params["state"][0])
            assert decoded_conn == connection_id

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_oidc_callback_provisions_existing_user_membership(
    db_session: AsyncSession,
    test_user,
    test_org: Organization,
    rsa_private_pem,
    rsa_public_jwk_dict,
    engine,
):
    """A returning SSO user (already in DB with an email) is reused; the
    membership is created against the connection's org."""
    from app.main import app
    from app.core.database import get_db
    from app.models.user import User
    from sqlalchemy import select, delete
    from sqlalchemy.ext.asyncio import async_sessionmaker

    # Existing user with the SSO email, but NOT a member of test_org
    existing = User(
        email="alice@corp.example.com",
        name="Alice Smith",
        password_hash="!sso-no-password",
        status="active",
    )
    db_session.add(existing)
    await db_session.flush()

    connection = SSOConnection(
        organization_id=test_org.id,
        protocol="oidc",
        name="Test IdP",
        issuer="https://accounts.example.com",
        client_id="test-client-id",
        client_secret_ref="TEST_OIDC_SECRET",
        default_role="member",
        enforce_sso=False,
    )
    db_session.add(connection)
    await db_session.flush()
    connection_id = connection.id

    sf = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with sf() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db

    state_nonce = "test-nonce-123"
    state_token = oidc_service.create_state_token(
        connection_id=connection_id, nonce=state_nonce
    )
    id_token = _make_id_token(
        rsa_private_pem,
        "test-kid-001",
        "https://accounts.example.com",
        "test-client-id",
        state_nonce,
        claims_extra={"email": "alice@corp.example.com", "name": "Alice Smith"},
    )
    token_response = {"id_token": id_token, "access_token": "idp-access-tok", "token_type": "Bearer"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        with patch.object(
            oidc_service, "discover_config",
            AsyncMock(return_value={
                "issuer": "https://accounts.example.com",
                "authorization_endpoint": "https://accounts.example.com/auth",
                "token_endpoint": "https://accounts.example.com/token",
                "jwks_uri": "https://accounts.example.com/.well-known/jwks",
            }),
        ), patch.object(
            oidc_service, "fetch_jwks",
            AsyncMock(return_value={"keys": [rsa_public_jwk_dict]}),
        ), patch.object(
            oidc_service, "exchange_code", AsyncMock(return_value=token_response)
        ):
            resp = await ac.get(
                "/api/v1/sso/oidc/callback",
                params={"code": "auth-code-xyz", "state": state_token, "redirect_uri": "http://localhost:3000/api/v1/sso/oidc/callback"},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["email"] == "alice@corp.example.com"

            # Same user record reused (not duplicated) and now a member
            from app.models.rbac import OrganizationMember

            users = (
                await db_session.execute(
                    select(User).where(User.email == "alice@corp.example.com")
                )
            ).scalars().all()
            assert len(users) == 1

            membership = (
                await db_session.execute(
                    select(OrganizationMember).where(
                        OrganizationMember.organization_id == test_org.id,
                        OrganizationMember.user_id == users[0].id,
                    )
                )
            ).scalar_one_or_none()
            assert membership is not None
            assert membership.status == "active"

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_oidc_start_domain_routing_public_endpoint(
    db_session: AsyncSession,
    test_org: Organization,
    engine,
):
    """POST /sso/oidc/start resolves a connection by email domain without
    authentication and returns an authorization URL."""
    from app.main import app
    from app.core.database import get_db
    from sqlalchemy.ext.asyncio import async_sessionmaker

    connection = SSOConnection(
        organization_id=test_org.id,
        protocol="oidc",
        name="Corp IdP",
        issuer="https://accounts.example.com",
        client_id="test-client-id",
        client_secret_ref="TEST_OIDC_SECRET",
        default_role="member",
        domains=["corp.example.com", "example.com"],
        enforce_sso=False,
    )
    db_session.add(connection)
    await db_session.flush()

    sf = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with sf() as s:
            yield s

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        with patch.object(
            oidc_service,
            "discover_config",
            AsyncMock(
                return_value={
                    "issuer": "https://accounts.example.com",
                    "authorization_endpoint": "https://accounts.example.com/auth",
                    "token_endpoint": "https://accounts.example.com/token",
                    "jwks_uri": "https://accounts.example.com/.well-known/jwks",
                }
            ),
        ):
            resp = await ac.post(
                "/api/v1/sso/oidc/start",
                json={"email": "jane@corp.example.com"},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["organization_name"] == "Corp IdP"
            assert body["authorization_url"].startswith(
                "https://accounts.example.com/auth"
            )

            # A domain with no match -> 404, not a leak
            miss = await ac.post(
                "/api/v1/sso/oidc/start",
                json={"email": "jane@unknown.org"},
            )
            assert miss.status_code == 404

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_oidc_callback_redirect_mode(
    db_session: AsyncSession,
    test_org: Organization,
    rsa_private_pem,
    rsa_public_jwk_dict,
    engine,
):
    """When frontend_redirect_uri is provided, the callback 307-redirects the
    browser to the SPA with tokens placed in the URL fragment."""
    from app.main import app
    from app.core.database import get_db
    from sqlalchemy.ext.asyncio import async_sessionmaker

    connection = SSOConnection(
        organization_id=test_org.id,
        protocol="oidc",
        name="Corp IdP",
        issuer="https://accounts.example.com",
        client_id="test-client-id",
        client_secret_ref="TEST_OIDC_SECRET",
        default_role="member",
        enforce_sso=False,
    )
    db_session.add(connection)
    await db_session.flush()
    connection_id = connection.id

    sf = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with sf() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db

    state_nonce = "test-nonce-123"
    state_token = oidc_service.create_state_token(
        connection_id=connection_id, nonce=state_nonce
    )
    id_token = _make_id_token(
        rsa_private_pem,
        "test-kid-001",
        "https://accounts.example.com",
        "test-client-id",
        state_nonce,
        claims_extra={"email": "bob@corp.example.com", "name": "Bob"},
    )
    token_response = {"id_token": id_token, "access_token": "idp-access-tok", "token_type": "Bearer"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        with patch.object(
            oidc_service, "discover_config",
            AsyncMock(return_value={
                "issuer": "https://accounts.example.com",
                "authorization_endpoint": "https://accounts.example.com/auth",
                "token_endpoint": "https://accounts.example.com/token",
                "jwks_uri": "https://accounts.example.com/.well-known/jwks",
            }),
        ), patch.object(
            oidc_service, "fetch_jwks",
            AsyncMock(return_value={"keys": [rsa_public_jwk_dict]}),
        ), patch.object(
            oidc_service, "exchange_code", AsyncMock(return_value=token_response)
        ):
            resp = await ac.get(
                "/api/v1/sso/oidc/callback",
                params={
                    "code": "auth-code-xyz",
                    "state": state_token,
                    "frontend_redirect_uri": "http://localhost:3000/sso/callback",
                },
                follow_redirects=False,
            )
            assert resp.status_code == 307, resp.text
            location = resp.headers["location"]
            assert location.startswith("http://localhost:3000/sso/callback#")
            assert "access_token=" in location
            assert "refresh_token=" in location
            assert "error" not in resp.text or "error" not in location

    app.dependency_overrides.clear()
