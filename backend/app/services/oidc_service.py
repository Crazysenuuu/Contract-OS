"""OIDC authorization-code flow for enterprise SSO (spec 1.21.8 / 2.15).

Implements the *live* IdP round-trip that the CRUD-only connection surface
deliberately deferred to a gateway: OIDC discovery, authorization-URL
construction, authorization-code exchange, and JWKS-backed ID-token
verification.

The flow is stateless by design: the OAuth ``state`` value is a short-lived
JWT signed with the platform secret that carries the connection id, a random
``nonce``, and an expiry. The ``nonce`` is replayed into the IdP authorization
request, so verifying the ``nonce`` claim of the returned ID token proves the
token belongs to this exact login attempt (anti-replay, spec 1.21.8.14).

Configuration secrets stay in the secret manager: ``client_secret_ref`` is a
reference, not a raw value. ``resolve_client_secret`` turns a reference into a
value by reading the referenced environment variable; the bare reference is
only accepted in non-production environments for local dev against a mock IdP.
"""

from __future__ import annotations

import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from jose import jwk, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings_lazy
from app.core.exceptions import UnauthenticatedError, ValidationError
from app.models.sso import IdentityProviderEvent, SSOConnection
from app.models.user import User
from app.services.tenant_context import tenant_scope

logger = logging.getLogger(__name__)
settings = get_settings_lazy()

OIDC_SCOPES = "openid profile email"
STATE_TTL_SECONDS = int(getattr(settings, "oidc_state_ttl_seconds", 600))


class OIDCError(Exception):
    """Base class for OIDC flow failures."""


class ProviderConfigError(OIDCError):
    """Discovery / signing configuration could not be obtained."""


class TokenValidationError(OIDCError):
    """Authorization-code exchange or ID-token verification failed."""


# --- Discovery -------------------------------------------------------------


async def discover_config(issuer: str) -> dict:
    """Fetch the OIDC discovery document for an issuer."""
    discovery_url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(discovery_url)
        if resp.status_code != 200:
            raise ProviderConfigError(
                f"Discovery failed ({resp.status_code}) for {issuer}"
            )
        return resp.json()


async def fetch_jwks(jwks_uri: str) -> dict:
    """Fetch the JWKS used to verify ID-token signatures."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(jwks_uri)
        if resp.status_code != 200:
            raise ProviderConfigError(
                f"JWKS fetch failed ({resp.status_code}) from {jwks_uri}"
            )
        return resp.json()


# --- Authorization request -------------------------------------------------


def create_state_token(*, connection_id: uuid.UUID, nonce: str) -> str:
    """Build the signed OAuth ``state`` carrying connection id + nonce."""
    expires = datetime.now(timezone.utc) + timedelta(seconds=STATE_TTL_SECONDS)
    payload = {
        "sub": str(connection_id),
        "nonce": nonce,
        "iat": int(datetime.now(timezone.utc).timestamp()),
        "exp": expires,
    }
    return jwt.encode(
        payload,
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def decode_state_token(state: str) -> tuple[uuid.UUID, str]:
    """Verify and decode the state JWT -> (connection_id, nonce)."""
    try:
        payload = jwt.decode(
            state,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
        )
        connection_id = uuid.UUID(payload["sub"])
        return connection_id, payload["nonce"]
    except Exception as exc:
        raise TokenValidationError("Invalid or expired SSO state") from exc


def build_authorization_url(
    *,
    discover: dict,
    client_id: str,
    redirect_uri: str,
    state: str,
    nonce: str,
) -> str:
    """Build the IdP authorization URL for an authorization-code request."""
    endpoint = discover.get("authorization_endpoint")
    if not endpoint:
        raise ProviderConfigError("Discovery document has no authorization_endpoint")
    # Keep the query short and deterministic; the extra params are read by
    # read_provider_metadata below.
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": OIDC_SCOPES,
        "state": state,
        "nonce": nonce,
    }
    # prompt/login_hint optional extras stay out — a well-behaved IdP accepts
    # these six parameters for a confidential authorization-code flow.
    separator = "&" if "?" in endpoint else "?"
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{endpoint}{separator}{query}"


# --- Token exchange --------------------------------------------------------


def resolve_client_secret(connection: SSOConnection) -> str | None:
    """Turn a secret-manager reference into a usable client secret.

    The reference is an environment-variable name (``CLIENT_SECRET`` or
    ``env:NAME``). This mirrors the platform rule that secrets live in the
    secret manager / environment — never in the database (spec 1.22.14).
    A bare reference is accepted only when the connection is unenforced or
    running outside production, strictly for local development against a mock
    IdP.
    """
    if not connection.client_secret_ref:
        return None
    ref = connection.client_secret_ref
    env_name = ref[4:] if ref.startswith("env:") else ref

    import os

    value = os.environ.get(env_name)
    if value is not None:
        return value
    if settings.environment == "production" and connection.enforce_sso:
        raise ProviderConfigError(
            f"SSO client secret reference {ref!r} is not resolvable in production"
        )
    return ref


async def exchange_code(
    *,
    discover: dict,
    client_id: str,
    client_secret: str | None,
    code: str,
    redirect_uri: str,
) -> dict:
    """Exchange the authorization code for tokens at the token endpoint."""
    endpoint = discover.get("token_endpoint")
    if not endpoint:
        raise ProviderConfigError("Discovery document has no token_endpoint")
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
    }
    if client_secret is not None:
        form["client_secret"] = client_secret
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(endpoint, data=form)
        if resp.status_code != 200:
            raise TokenValidationError(
                f"Token exchange failed ({resp.status_code})"
            )
        return resp.json()


# --- ID-token verification -------------------------------------------------


def _matching_signing_key(jwks: dict, kid: str | None):
    keys = jwks.get("keys", [])
    if kid is not None:
        for candidate in keys:
            if candidate.get("kid") == kid:
                return candidate
    # Some providers omit kid on single-key JWKS.
    if len(keys) == 1:
        return keys[0]
    return None


def verify_id_token(
    *,
    id_token: str,
    jwks: dict,
    issuer: str,
    client_id: str,
    nonce: str,
    idp_user_id: str | None = None,
) -> dict:
    """Verify an ID token signature + claims and return its payload.

    Signature is verified against the provider's JWKS. Claims verified:
    ``iss`` equals the connection issuer, ``aud`` contains the client id,
    ``exp`` is in the future, and ``nonce`` matches the login attempt.
    """
    try:
        header = jwt.get_unverified_header(id_token)
    except Exception as exc:
        raise TokenValidationError("Malformed ID token header") from exc

    key = _matching_signing_key(jwks, header.get("kid"))
    if key is None:
        raise TokenValidationError("No matching signing key in provider JWKS")

    try:
        signing_key = jwk.construct(key, algorithm=header.get("alg", "RS256"))
    except Exception as exc:
        raise TokenValidationError(f"Cannot construct signing key: {exc}") from exc

    try:
        payload = jwt.decode(
            id_token,
            signing_key,
            algorithms=[header.get("alg", "RS256")],
            audience=client_id,
            issuer=issuer,
            options={
                "verify_at_hash": False,
                "verify_nonce": False,
            },
        )
    except Exception as exc:
        raise TokenValidationError(f"ID token verification failed: {exc}") from exc

    if payload.get("nonce") != nonce:
        raise TokenValidationError("ID token nonce mismatch (replay detected)")
    if idp_user_id and payload.get("sub") != idp_user_id:
        raise TokenValidationError("ID token subject does not match expected user")
    return payload


# --- Provisioning ----------------------------------------------------------

async def provision_sso_user(
    db: AsyncSession,
    *,
    connection: SSOConnection,
    claims: dict,
    idp_user_id: str | None = None,
) -> tuple[User, uuid.UUID]:
    """Create or resolve a user + membership for an SSO login.

    Mirrors SCIM provisioning semantics (create / reactivate / preserve legal
    records), scoped to the connection's organization. Returns (user, org_id).
    """
    email = (claims.get("email") or "").strip().lower()
    if not email or "@" not in email:
        raise ValidationError("IdP must return a valid email claim")

    from app.models.rbac import OrganizationMember, Role

    org_id = connection.organization_id

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None:
        given = claims.get("given_name") or claims.get("name") or email.split("@")[0]
        user = User(
            email=email,
            name=claims.get("name") or given or email.split("@")[0],
            # SSO users have no local password until they set one.
            password_hash="!sso-no-password",
            status="active",
        )
        db.add(user)
        await db.flush()

    if user.status in ("deactivated", "disabled"):
        user.status = "active"

    membership_result = await db.execute(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == org_id,
            OrganizationMember.user_id == user.id,
        )
    )
    membership = membership_result.scalar_one_or_none()
    if membership is None:
        role_result = await db.execute(
            select(Role).where(
                Role.organization_id == org_id,
                Role.name == (connection.default_role or "member"),
            ).limit(1)
        )
        role = role_result.scalar_one_or_none()
        if role is None and connection.default_role:
            role_result = await db.execute(
                select(Role).where(Role.organization_id == org_id).limit(1)
            )
            role = role_result.scalar_one_or_none()
        # Provisioning writes tenant rows from a callback that never passed
        # through the request-scoped tenant dependency, so RLS context has to
        # be pinned here explicitly.
        async with tenant_scope(db, org_id):
            db.add(
                OrganizationMember(
                    organization_id=org_id,
                    user_id=user.id,
                    role_id=role.id if role else None,
                    status="active",
                )
            )
    elif membership.status != "active":
        async with tenant_scope(db, org_id):
            membership.status = "active"

    db.add(
        IdentityProviderEvent(
            organization_id=org_id,
            event_type="sso_login",
            idp_user_id=idp_user_id or claims.get("sub"),
            email=email,
            succeeded=True,
            detail={
                "protocol": "oidc",
                "connection_id": str(connection.id),
                "connection": connection.name,
                "claims_sub": claims.get("sub"),
            },
        )
    )
    await db.flush()
    return user, org_id


# --- Convenience ------------------------------------------------------------------


async def read_provider_metadata(*, autor_url: str):
    """Parse the authorization URL back into its configurable parameters.

    Used by the callback (and tests) to reconstruct discovery + params without
    a second IdP round-trip. Kept tiny on purpose; callers that need discovery
    call discover_config directly.
    """
    from urllib.parse import parse_qs, urlsplit

    parsed = urlsplit(autor_url)
    query = parse_qs(parsed.query)
    return {k: v[0] for k, v in query.items()}