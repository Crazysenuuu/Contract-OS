"""SSO + SCIM API (spec 1.21.8-1.21.9, 2.15).

Admin surface for SSO connections and token issuance, plus the SCIM 2.0
provisioning endpoints consumed by identity providers. The OIDC endpoints
implement the live authorization-code flow (discovery -> authorize -> callback)
that the connection-admin surface defers to a gateway in production.
"""

import secrets
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings_lazy
from app.core.database import get_db
from app.core.security import create_access_token
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.dependencies.rbac import require_permission
from app.models.sso import SSOConnection
from app.models.user import User
from app.services import oidc_service, scim_service
from app.services.auth_security_service import create_refresh_token

router = APIRouter(prefix="/sso", tags=["SSO"])
scim_router = APIRouter(prefix="/scim/v2", tags=["SCIM"])

_perm_org_manage_sso = Depends(require_permission("org.manage_sso"))

settings = get_settings_lazy()


class SSOConnectionCreate(BaseModel):
    protocol: str = Field(pattern="^(saml|oidc)$")
    name: str
    issuer: Optional[str] = None
    client_id: Optional[str] = None
    client_secret_ref: Optional[str] = None
    idp_metadata_url: Optional[str] = None
    domains: list[str] = Field(default_factory=list)
    default_role: Optional[str] = None
    enforce_sso: bool = False


def _connection_dict(c: SSOConnection) -> dict:
    return {
        "id": str(c.id),
        "protocol": c.protocol,
        "name": c.name,
        "issuer": c.issuer,
        "client_id": c.client_id,
        "domains": c.domains or [],
        "default_role": c.default_role,
        "enforce_sso": c.enforce_sso,
        "enabled": c.enabled,
    }


@router.get("/connections", response_model=list[dict])
async def list_connections(
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SSOConnection).where(SSOConnection.organization_id == org_id)
    )
    return [_connection_dict(c) for c in result.scalars().all()]


@router.post("/connections", status_code=status.HTTP_201_CREATED, dependencies=[_perm_org_manage_sso])
async def create_connection(
    data: SSOConnectionCreate,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    connection = SSOConnection(
        organization_id=org_id,
        protocol=data.protocol,
        name=data.name,
        issuer=data.issuer,
        client_id=data.client_id,
        client_secret_ref=data.client_secret_ref,
        idp_metadata_url=data.idp_metadata_url,
        domains={"domains": data.domains},
        default_role=data.default_role,
        enforce_sso=data.enforce_sso,
    )
    db.add(connection)
    await db.commit()
    await db.refresh(connection)
    return _connection_dict(connection)


@router.post("/connections/{connection_id}/test", status_code=status.HTTP_200_OK)
async def test_connection(
    connection_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Connection health check: validates configuration completeness.
    A live IdP round-trip belongs to the deployment's SSO gateway."""
    result = await db.execute(
        select(SSOConnection).where(
            SSOConnection.id == connection_id,
            SSOConnection.organization_id == org_id,
        )
    )
    connection = result.scalar_one_or_none()
    if connection is None:
        raise HTTPException(status_code=404, detail="SSO connection not found")

    problems = []
    if connection.protocol == "oidc":
        if not connection.issuer or not connection.client_id or not connection.client_secret_ref:
            problems.append("OIDC requires issuer, client_id and client_secret_ref")
    else:
        if not connection.idp_metadata_url and not connection.idp_certificate:
            problems.append("SAML requires idp_metadata_url or idp_certificate")
    return {"ok": not problems, "problems": problems}


class SCIMTokenCreate(BaseModel):
    name: str


@router.post("/scim-tokens", status_code=status.HTTP_201_CREATED, dependencies=[_perm_org_manage_sso])
async def create_scim_token(
    data: SCIMTokenCreate,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Issue a SCIM bearer token. The raw token is shown exactly once."""
    raw = await scim_service.issue_scim_token(db, organization_id=org_id, name=data.name)
    await db.commit()
    return {"token": raw, "note": "Store this token now - it will not be shown again"}


# --- OIDC authorization-code flow (spec 1.21.8) ---------------------------


class OIDCStartBody(BaseModel):
    """Pre-auth SSO entry point: resolve a connection by email domain."""

    email: str = Field(..., min_length=3)
    oidc_redirect_uri: str | None = None


@router.post("/oidc/start", response_model=dict)
async def oidc_start(
    body: OIDCStartBody,
    db: AsyncSession = Depends(get_db),
):
    """Public SSO kickoff used by the login page.

    Resolves an enabled OIDC connection by the caller's email domain and
    returns the IdP ``authorization_url`` for the browser to follow. No
    authenticated context is required — this is pre-auth by design.
    """
    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(status_code=400, detail="Enter a valid email address")
    domain = email.rsplit("@", 1)[1].lower()

    result = await db.execute(
        select(SSOConnection).where(
            SSOConnection.enabled.is_(True),
            SSOConnection.protocol == "oidc",
        )
    )
    connections = result.scalars().all()

    matched = [c for c in connections if c.domains and domain in c.domains]
    if not matched and len(connections) == 1:
        # Single-tenant convenience: an org with exactly one enabled OIDC
        # connection routes every SSO sign-in to it.
        matched = [connections[0]]
    if not matched:
        raise HTTPException(status_code=404, detail="No SSO configured for this domain")

    connection = matched[0]
    if not connection.issuer or not connection.client_id:
        raise HTTPException(status_code=400, detail="SSO connection is not fully configured")

    try:
        discovery = await oidc_service.discover_config(connection.issuer)
    except oidc_service.OIDCError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    callback_uri = body.oidc_redirect_uri or f"{settings.app_base_url}/api/v1/sso/oidc/callback"
    nonce = secrets.token_urlsafe(24)
    state = oidc_service.create_state_token(
        connection_id=connection.id, nonce=nonce
    )
    authorization_url = oidc_service.build_authorization_url(
        discover=discovery,
        client_id=connection.client_id,
        redirect_uri=callback_uri,
        state=state,
        nonce=nonce,
    )
    return {
        "authorization_url": authorization_url,
        "redirect_uri": callback_uri,
        "connection_id": str(connection.id),
        "organization_name": connection.name,
    }


@router.get("/oidc/{connection_id}/authorize")
async def oidc_authorize(
    connection_id: uuid.UUID,
    redirect_uri: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Build the IdP authorization URL for a configured OIDC connection.

    Returns ``authorization_url`` that the frontend redirects the browser
    to. The OAuth ``state`` is a short-lived signed token carrying the
    connection id and a random ``nonce``; the callback verifies the nonce
    inside the returned ID token (anti-replay).
    """
    result = await db.execute(
        select(SSOConnection).where(
            SSOConnection.id == connection_id,
            SSOConnection.organization_id == org_id,
            SSOConnection.enabled.is_(True),
        )
    )
    connection = result.scalar_one_or_none()
    if connection is None or connection.protocol != "oidc":
        raise HTTPException(status_code=404, detail="OIDC connection not found")
    if not connection.issuer or not connection.client_id:
        raise HTTPException(status_code=400, detail="Connection is not fully configured")

    try:
        discovery = await oidc_service.discover_config(connection.issuer)
    except oidc_service.OIDCError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    callback_uri = redirect_uri or f"{settings.app_base_url}/api/v1/sso/oidc/callback"
    nonce = secrets.token_urlsafe(24)
    state = oidc_service.create_state_token(
        connection_id=connection.id, nonce=nonce
    )
    authorization_url = oidc_service.build_authorization_url(
        discover=discovery,
        client_id=connection.client_id,
        redirect_uri=callback_uri,
        state=state,
        nonce=nonce,
    )
    return {"authorization_url": authorization_url, "redirect_uri": callback_uri}


class OIDCCallbackParams(BaseModel):
    code: str
    state: str


@router.get("/oidc/callback", response_model=dict)
async def oidc_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    redirect_uri: str | None = None,
    frontend_redirect_uri: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Handle the IdP authorization-code redirect back to the platform.

    Verifies the signed state, exchanges the code for tokens, verifies the ID
    token (signature + issuer + audience + nonce), provisions the user, and
    returns the platform access/refresh tokens.

    Two delivery modes:
    - ``frontend_redirect_uri`` set: 307-redirect the browser to that SPA URL
      with the tokens in the URL fragment (never in the query string, so they
      are not logged). This is the mode used by the login-page SSO flow.
    - otherwise: JSON response for API / native clients.
    """
    if error:
        raise HTTPException(
            status_code=400,
            detail=f"IdP authorization error: {error} ({error_description or ''})",
        )
    if not code or not state:
        raise HTTPException(status_code=400, detail="code and state are required")

    try:
        connection_id, nonce = oidc_service.decode_state_token(state)
    except oidc_service.OIDCError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    result = await db.execute(
        select(SSOConnection).where(
            SSOConnection.id == connection_id,
            SSOConnection.enabled.is_(True),
        )
    )
    connection = result.scalar_one_or_none()
    if connection is None or connection.protocol != "oidc":
        raise HTTPException(status_code=404, detail="OIDC connection not found")

    try:
        discovery = await oidc_service.discover_config(connection.issuer)
        client_secret = oidc_service.resolve_client_secret(connection)
        tokens = await oidc_service.exchange_code(
            discover=discovery,
            client_id=connection.client_id,
            client_secret=client_secret,
            code=code,
            redirect_uri=redirect_uri or f"{settings.app_base_url}/api/v1/sso/oidc/callback",
        )
        id_token = tokens.get("id_token")
        if not id_token:
            raise oidc_service.TokenValidationError("IdP returned no id_token")
        jwks = await oidc_service.fetch_jwks(discovery["jwks_uri"])
        claims = oidc_service.verify_id_token(
            id_token=id_token,
            jwks=jwks,
            issuer=connection.issuer,
            client_id=connection.client_id,
            nonce=nonce,
        )
    except oidc_service.OIDCError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    user, org_id = await oidc_service.provision_sso_user(
        db, connection=connection, claims=claims
    )
    await db.flush()

    access_token = create_access_token(
        user_id=user.id,
        organization_id=org_id,
    )
    _, refresh_token = await create_refresh_token(
        db,
        user_id=user.id,
        session_id=None,
    )
    await db.commit()

    if frontend_redirect_uri:
        import urllib.parse

        separator = "#" if "#" not in frontend_redirect_uri else "&"
        fragments = urllib.parse.urlencode(
            {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "user_id": str(user.id),
                "organization_id": str(org_id),
            }
        )
        from fastapi.responses import RedirectResponse

        return RedirectResponse(
            url=f"{frontend_redirect_uri}{separator}{fragments}",
            status_code=307,
        )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "user_id": user.id,
        "email": user.email,
        "organization_id": org_id,
    }


# --- SCIM 2.0 server surface --------------------------------------------


class SCIMUserPost(BaseModel):
    userName: str
    emails: list[dict] = Field(default_factory=list)
    name: Optional[dict] = None
    active: bool = True


def _org_from_scim_auth(
    authorization: str | None, db: AsyncSession
) -> uuid.UUID:
    try:
        return scim_service.authenticate_scim_token(
            db, authorization_header=authorization
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail=str(exc))


@scim_router.post("/Users", status_code=status.HTTP_201_CREATED)
async def scim_create_user(
    data: SCIMUserPost,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    org_id = _org_from_scim_auth(authorization, db)
    email = next(
        (e.get("value") for e in data.emails if e.get("primary")),
        data.emails[0].get("value") if data.emails else None,
    )
    try:
        result = await scim_service.provision_user(
            db,
            organization_id=org_id,
            user_name=data.userName,
            email=email,
            given_name=(data.name or {}).get("givenName"),
            family_name=(data.name or {}).get("familyName"),
            active=data.active,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await db.commit()
    return result


@scim_router.patch("/Users/{user_id}")
async def scim_patch_user(
    user_id: uuid.UUID,
    data: dict,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    """SCIM PATCH: the common operation is {'active': true/false} via
    the 'Replace' op on the 'active' path."""
    org_id = _org_from_scim_auth(authorization, db)
    active = None
    for op in data.get("Operations", []):
        if op.get("op", "").lower() in ("replace", "add") and op.get("path") == "active":
            active = bool(op.get("value"))
    if active is None and isinstance(data.get("active"), bool):
        active = data["active"]
    try:
        result = await scim_service.update_user(
            db, organization_id=org_id, user_id=user_id, active=active
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await db.commit()
    return result


@scim_router.delete("/Users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def scim_delete_user(
    user_id: uuid.UUID,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    org_id = _org_from_scim_auth(authorization, db)
    await scim_service.deactivate_user(db, organization_id=org_id, user_id=user_id)
    await db.commit()
