import re
import uuid
import secrets
import logging
import pyotp
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import (
    create_access_token,
    hash_password,
    verify_password,
)
from app.dependencies.auth import get_current_user
from app.models.organization import Organization
from app.models.rbac import OrganizationMember, Role
from app.models.user import User, UserSession
from app.schemas.auth import (
    TokenResponse,
    UserLogin,
    UserRegister,
    UserResponse,
    MFAVerify,
    MFASetupResponse,
    EmailVerify,
    RefreshRequest,
)
from app.services.auth_security_service import (
    check_login_rate_limit,
    create_refresh_token,
    record_login_attempt,
    revoke_all_for_user,
    revoke_refresh_token,
    rotate_refresh_token,
    validate_refresh_token,
)
from app.tasks.email_tasks import send_email_async

router = APIRouter(prefix="/auth", tags=["auth"])

logger = logging.getLogger(__name__)


def generate_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug


def _client_ip(request: Request) -> str | None:
    if request.client is None:
        return None
    return request.client.host


async def _create_session(
    db: AsyncSession,
    user_id: uuid.UUID,
    organization_id: uuid.UUID | None,
    request: Request,
) -> None:
    session = UserSession(
        user_id=user_id,
        organization_id=organization_id,
        login_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        status="active",
    )
    db.add(session)


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    data: UserRegister,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(
        select(User).where(User.email == data.email)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    user = User(
        email=data.email,
        name=data.name,
        password_hash=hash_password(data.password),
        status="pending_verification",
        verification_token=secrets.token_urlsafe(32),
    )
    db.add(user)
    await db.flush()

    # Send verification email. Celery is used when the broker is available,
    # but registration must never block or fail if Redis/Celery is down.
    # `retry=False` skips kombu's minutes-long broker publish retries, and
    # `ignore_result=True` skips the Redis result-backend handshake — without
    # it `on_task_call` opens its own connection and burns through its retry
    # limit when Redis is unreachable, hanging the HTTP request.
    try:
        send_email_async.apply_async(
            kwargs={
                "to_email": user.email,
                "subject": "Verify your ContractOS Account",
                "template_name": "welcome_email",
                "template_data": {
                    "name": user.name,
                    "token": user.verification_token,
                    "verify_url": f"http://localhost:3000/verify-email?token={user.verification_token}",
                },
            },
            retry=False,
            ignore_result=True,
        )
    except Exception:
        logger.exception("Failed to queue welcome email; continuing registration")

    slug = generate_slug(data.name + "-" + str(uuid.uuid4())[:8])

    org = Organization(
        name=data.name + "'s Organization",
        slug=slug,
        country="LK",
        timezone="Asia/Colombo",
    )
    db.add(org)
    await db.flush()

    owner_role = Role(
        organization_id=org.id,
        name="owner",
    )
    db.add(owner_role)
    await db.flush()

    membership = OrganizationMember(
        organization_id=org.id,
        user_id=user.id,
        role_id=owner_role.id,
        status="active",
    )
    db.add(membership)
    await db.flush()

    await _create_session(db, user.id, org.id, request)

    token = create_access_token(
        user_id=user.id,
        organization_id=org.id,
    )
    _, refresh_token = await create_refresh_token(
        db,
        user_id=user.id,
        session_id=None,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )

    return TokenResponse(
        access_token=token,
        refresh_token=refresh_token,
        user_id=user.id,
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    data: UserLogin,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    ip = _client_ip(request)
    await check_login_rate_limit(db, data.email, ip)

    result = await db.execute(
        select(User).where(User.email == data.email)
    )
    user = result.scalar_one_or_none()

    if user is None or not verify_password(
        data.password, user.password_hash
    ):
        await record_login_attempt(
            db,
            email=data.email,
            ip_address=ip,
            success=False,
            reason="invalid_credentials",
        )
        # Commit BEFORE raising — the 401 would otherwise roll back the
        # attempt record, defeating the rate limiter.
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if user.mfa_enabled:
        if not data.mfa_code:
            await record_login_attempt(
                db, email=data.email, ip_address=ip, success=False, reason="mfa_required"
            )
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="MFA code required",
                headers={"WWW-Authenticate": "Bearer error=\"mfa_required\""},
            )
        if not user.mfa_secret:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="MFA is enabled but no secret is configured",
            )
        totp = pyotp.TOTP(user.mfa_secret)
        if not totp.verify(data.mfa_code):
            await record_login_attempt(
                db, email=data.email, ip_address=ip, success=False, reason="invalid_mfa"
            )
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid MFA code",
            )

    if user.status not in ("active", "pending_verification"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is not active",
        )

    membership_result = await db.execute(
        select(OrganizationMember.organization_id)
        .where(
            OrganizationMember.user_id == user.id,
            OrganizationMember.status == "active",
        )
        .limit(1)
    )
    org_id = membership_result.scalar_one_or_none()

    await _create_session(db, user.id, org_id, request)

    token = create_access_token(
        user_id=user.id,
        organization_id=org_id,
    )
    _, refresh_token = await create_refresh_token(
        db,
        user_id=user.id,
        session_id=None,
        ip_address=ip,
        user_agent=request.headers.get("user-agent"),
    )
    await record_login_attempt(
        db, email=data.email, ip_address=ip, success=True, reason="ok"
    )

    return TokenResponse(
        access_token=token,
        refresh_token=refresh_token,
        user_id=user.id,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    data: RefreshRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Rotate a refresh token and issue a fresh access token."""
    validated = await validate_refresh_token(db, data.refresh_token)
    if validated is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )
    user, token_row = validated

    try:
        _, new_refresh = await rotate_refresh_token(
            db,
            token_row=token_row,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except HTTPException:
        raise

    membership_result = await db.execute(
        select(OrganizationMember.organization_id)
        .where(
            OrganizationMember.user_id == user.id,
            OrganizationMember.status == "active",
        )
        .limit(1)
    )
    org_id = membership_result.scalar_one_or_none()

    token = create_access_token(
        user_id=user.id,
        organization_id=org_id,
    )
    return TokenResponse(
        access_token=token,
        refresh_token=new_refresh,
        user_id=user.id,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Close the user's active sessions and revoke all refresh tokens."""
    await db.execute(
        update(UserSession)
        .where(
            UserSession.user_id == current_user.id,
            UserSession.status == "active",
        )
        .values(
            status="logged_out",
            logout_at=datetime.now(timezone.utc),
        )
    )
    await revoke_all_for_user(db, current_user.id, reason="logout")


@router.get("/me", response_model=UserResponse)
async def get_me(
    current_user: User = Depends(get_current_user),
):
    return current_user


@router.post("/verify-email")
async def verify_email(
    data: EmailVerify,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).where(User.verification_token == data.token)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token",
        )

    user.status = "active"
    user.email_verified_at = datetime.now(timezone.utc)
    user.verification_token = None
    await db.commit()

    return {"message": "Email verified successfully"}


@router.post("/mfa/setup", response_model=MFASetupResponse)
async def setup_mfa(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.mfa_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA is already enabled",
        )

    secret = pyotp.random_base32()
    current_user.mfa_secret = secret
    await db.commit()

    provisioning_uri = pyotp.totp.TOTP(secret).provisioning_uri(
        name=current_user.email,
        issuer_name="ContractOS"
    )

    return MFASetupResponse(
        secret=secret,
        provisioning_uri=provisioning_uri
    )


@router.post("/mfa/verify")
async def verify_mfa_setup(
    data: MFAVerify,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.mfa_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA is already enabled",
        )

    if not current_user.mfa_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA setup has not been initiated",
        )

    totp = pyotp.TOTP(current_user.mfa_secret)
    if not totp.verify(data.code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid MFA code",
        )

    current_user.mfa_enabled = True
    await db.commit()

    return {"message": "MFA enabled successfully"}
