import re
import uuid
import secrets
import logging
import pyotp
from datetime import date, datetime, timedelta, timezone

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
    MFADisable,
    EmailVerify,
    RefreshRequest,
    PasswordChange,
    ProfileUpdate,
    SessionResponse,
    ResendVerificationRequest,
    ResendVerificationResponse,
)
from app.services.auth_security_service import (
    MAX_FAILED_ATTEMPTS_PER_EMAIL,
    check_login_rate_limit,
    create_refresh_token,
    record_login_attempt,
    revoke_all_for_user,
    revoke_refresh_token,
    rotate_refresh_token,
    validate_refresh_token,
)
from app.services.tenant_context import tenant_scope
from app.tasks.email_tasks import send_email_async

router = APIRouter(prefix="/auth", tags=["auth"])

logger = logging.getLogger(__name__)


def generate_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug


def _absolute_url(path: str) -> str:
    """Resolve an app path against the configured public base URL.

    Email and push payloads must never embed a developer's localhost: a
    verification link pointing at http://localhost:3000 is unopenable for
    every real recipient, which silently strands the account in
    ``pending_verification`` forever.
    """
    from app.core.config import get_settings_lazy

    base = (get_settings_lazy().app_base_url or "").rstrip("/")
    return f"{base}{path}" if path.startswith("/") else f"{base}/{path}"


def _totp_valid(user: User, mfa_code: str | None) -> bool | None:
    """Verify a TOTP code against the user's secret.

    Returns None when the user has MFA enabled but no secret configured
    (a 500-worthy server state the caller must handle), True/False for the
    verification result. Shared by /login and /admin/login so both paths
    verify codes identically.
    """
    if not user.mfa_enabled:
        return True
    if not user.mfa_secret:
        return None
    totp = pyotp.TOTP(user.mfa_secret)
    return totp.verify(mfa_code or "")


async def _first_active_org(db: AsyncSession, user_id: uuid.UUID) -> uuid.UUID | None:
    """Resolve the user's first active membership org (login org context)."""
    result = await db.execute(
        select(OrganizationMember.organization_id)
        .where(
            OrganizationMember.user_id == user_id,
            OrganizationMember.status == "active",
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


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

    # COPPA age gate — the schema validator already rejected under-13 DOBs
    # with 422; this is the last-line server-side check before an account is
    # created. Only the derived boolean is stored, never the DOB itself.
    today = date.today()
    age = (
        today.year
        - data.date_of_birth.year
        - ((today.month, today.day) < (data.date_of_birth.month, data.date_of_birth.day))
    )
    if age < 13:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="You must be at least 13 years old to create an account",
        )

    user = User(
        email=data.email,
        name=data.name,
        password_hash=hash_password(data.password),
        status="pending_verification",
        verification_token=secrets.token_urlsafe(32),
        is_adult=age >= 13,
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
                    "verify_url": _absolute_url(
                        f"/verify-email?token={user.verification_token}"
                    ),
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

    # Onboarding is the one flow that writes tenant rows before a tenant
    # context can exist: there is no membership yet for the request-scoped
    # dependency to resolve. Pin the context explicitly, otherwise
    # row-level security on roles/organization_members makes signup fail.
    async with tenant_scope(db, org.id):
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
        verification_required=user.status == "pending_verification",
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
        # Known user but wrong password → attribute the failure to their
        # org's audit chain so security monitoring can correlate it.
        failed_tenant = None
        if user is not None:
            failed_tenant = await db.scalar(
                select(OrganizationMember.organization_id).where(
                    OrganizationMember.user_id == user.id,
                    OrganizationMember.status == "active",
                ).limit(1)
            )
        await record_login_attempt(
            db,
            email=data.email,
            ip_address=ip,
            success=False,
            reason="invalid_credentials",
            tenant_id=failed_tenant,
            actor_id=user.id if user else None,
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
        totp_ok = _totp_valid(user, data.mfa_code)
        if totp_ok is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="MFA is enabled but no secret is configured",
            )
        if not totp_ok:
            await record_login_attempt(
                db, email=data.email, ip_address=ip, success=False, reason="invalid_mfa"
            )
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid MFA code",
            )

    if user.status == "pending_verification":
        # Not a generic 403: the caller knows the credentials were correct
        # and needs to know the specific next step. Issuing a session here
        # instead would be worse — get_current_user rejects
        # pending_verification on every subsequent request, so the token
        # would authenticate nothing while appearing to succeed.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email not verified. Check your inbox for the confirmation link.",
            headers={"X-Auth-Status": "verification_required"},
        )

    if user.status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is not active",
        )

    org_id = await _first_active_org(db, user.id)

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
        db,
        email=data.email,
        ip_address=ip,
        success=True,
        reason="ok",
        tenant_id=org_id,
        actor_id=user.id,
    )

    return TokenResponse(
        access_token=token,
        refresh_token=refresh_token,
        user_id=user.id,
    )


@router.post("/admin/login", response_model=TokenResponse)
async def admin_login(
    data: UserLogin,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Dedicated, hardened login endpoint for System Admins (Panels.txt).

    Differences from the generic /login:
    - enforces MFA unconditionally (no successful login without a valid TOTP,
      even if the flag was somehow left off the account),
    - rejects any account whose is_admin flag is not set (no user
      enumeration — non-admin credentials fail exactly like wrong passwords),
    - stricter rate-limit budget than end-user logins.
    """
    ip = _client_ip(request)
    # Stricter per-email failure budget than the generic /login endpoint —
    # admin credentials are a higher-value target (spec 1.22 hardening).
    await check_login_rate_limit(
        db, data.email, ip, max_failed_attempts=max(3, MAX_FAILED_ATTEMPTS_PER_EMAIL // 2)
    )

    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()

    def _fail() -> None:
        # Uniform error: no user enumeration via the admin endpoint.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email, password, or MFA code",
        )

    if user is None or not verify_password(data.password, user.password_hash):
        await record_login_attempt(
            db,
            email=data.email,
            ip_address=ip,
            success=False,
            reason="invalid_credentials",
            tenant_id=None,
            actor_id=user.id if user else None,
        )
        await db.commit()
        _fail()

    # Hardened path: an admin endpoint never issues tokens for a
    # non-admin account, and never without MFA.
    if not user.is_admin:
        await record_login_attempt(
            db,
            email=data.email,
            ip_address=ip,
            success=False,
            reason="not_admin",
        )
        await db.commit()
        _fail()

    if not data.mfa_code:
        await record_login_attempt(
            db,
            email=data.email,
            ip_address=ip,
            success=False,
            reason="mfa_required",
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="MFA code required",
            headers={"WWW-Authenticate": "Bearer error=\"mfa_required\""},
        )

    totp_ok = _totp_valid(user, data.mfa_code)
    if totp_ok is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="MFA is enabled but no secret is configured",
        )
    if not totp_ok:
        await record_login_attempt(
            db,
            email=data.email,
            ip_address=ip,
            success=False,
            reason="invalid_mfa",
        )
        await db.commit()
        _fail()

    if user.status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is not active",
        )

    org_id = await _first_active_org(db, user.id)

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
        db,
        email=data.email,
        ip_address=ip,
        success=True,
        reason="admin_login_ok",
        tenant_id=org_id,
        actor_id=user.id,
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


@router.post(
    "/resend-verification",
    response_model=ResendVerificationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def resend_verification(
    request: Request,
    data: ResendVerificationRequest,
    db: AsyncSession = Depends(get_db),
):
    """Re-send the confirmation email for an unverified account.

    Answers 202 for every input, whether or not an account exists and
    whether or not it is already verified: a different response would turn
    this endpoint into an account-existence oracle for anyone who can post
    an address. Real work is skipped for unknown or verified addresses so
    the endpoint cannot be used to send mail on someone's behalf either.
    """
    result = await db.execute(
        select(User).where(User.email == data.email)
    )
    user = result.scalar_one_or_none()

    if user is not None and user.status == "pending_verification":
        # Rotate the token so a previously leaked or expired link stops
        # working and only the newest email is honoured.
        user.verification_token = secrets.token_urlsafe(32)
        try:
            send_email_async.apply_async(
                kwargs={
                    "to_email": user.email,
                    "subject": "Verify your ContractOS Account",
                    "template_name": "welcome_email",
                    "template_data": {
                        "name": user.name,
                        "token": user.verification_token,
                        "verify_url": _absolute_url(
                            f"/verify-email?token={user.verification_token}"
                        ),
                    },
                },
                retry=False,
                ignore_result=True,
            )
        except Exception:
            logger.exception("Failed to queue verification email; continuing")
    else:
        logger.info("Resend verification requested for a non-pending address")

    return ResendVerificationResponse(
        message="If that address needs verification, a new link is on its way.",
        )


# ---------------------------------------------------------------------------
# Profile & account security (Panels.txt user portal: profile management,
# password change, MFA management, active sessions).
# ---------------------------------------------------------------------------


@router.patch("/me", response_model=UserResponse)
async def update_me(
    data: ProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update the current user's own profile fields (name, phone)."""
    update_data = data.model_dump(exclude_unset=True)
    if "name" in update_data and not (update_data["name"] or "").strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Name cannot be empty",
        )
    for field, value in update_data.items():
        setattr(current_user, field, value)
    await db.commit()
    await db.refresh(current_user)
    return current_user


@router.post("/me/password")
async def change_password(
    data: PasswordChange,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Change the current user's password.

    Requires the current password. On success every other session is
    logged out and all refresh tokens are revoked so a stolen session
    cannot survive a password rotation; the caller's own session is kept.
    """
    if not verify_password(data.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )
    if len(data.new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="New password must be at least 8 characters",
        )
    if data.new_password == data.current_password:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="New password must be different from the current password",
        )

    current_user.password_hash = hash_password(data.new_password)
    await db.flush()

    # Kill every other active session + revoke refresh tokens (spec 1.22:
    # password change invalidates existing sessions). The row for the
    # caller's current session is not tracked per-request, so "other
    # sessions" is approximated by logging out everything EXCEPT sessions
    # seen in the last 60 seconds from the same IP — the caller's.
    keep_after = datetime.now(timezone.utc) - timedelta(seconds=60)
    await db.execute(
        update(UserSession)
        .where(
            UserSession.user_id == current_user.id,
            UserSession.status == "active",
            UserSession.ip_address != _client_ip(request),
            UserSession.login_at < keep_after,
        )
        .values(
            status="logged_out",
            logout_at=datetime.now(timezone.utc),
        )
    )
    await revoke_all_for_user(db, current_user.id, reason="password_change")

    return {"message": "Password updated; other sessions were signed out"}


@router.post("/mfa/disable")
async def disable_mfa(
    data: MFADisable,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Disable MFA. Requires BOTH the account password and a valid TOTP
    code — knowing one factor alone must not be enough to strip the second
    (spec 1.22 hardening)."""
    if not current_user.mfa_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA is not enabled",
        )
    if not verify_password(data.password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password is incorrect",
        )
    totp_ok = _totp_valid(current_user, data.code)
    if totp_ok is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="MFA is enabled but no secret is configured",
        )
    if not totp_ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid MFA code",
        )

    current_user.mfa_enabled = False
    current_user.mfa_secret = None
    await db.commit()

    return {"message": "MFA disabled successfully"}


@router.get("/me/sessions", response_model=list[SessionResponse])
async def list_my_sessions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List the current user's login sessions, newest first."""
    result = await db.execute(
        select(UserSession)
        .where(UserSession.user_id == current_user.id)
        .order_by(UserSession.login_at.desc())
        .limit(50)
    )
    return result.scalars().all()


@router.post("/me/sessions/{session_id}/revoke")
async def revoke_my_session(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke one of the current user's own sessions ("sign out device")."""
    result = await db.execute(
        select(UserSession).where(
            UserSession.id == session_id,
            UserSession.user_id == current_user.id,
        )
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    if session.status == "active":
        session.status = "revoked"
        session.logout_at = datetime.now(timezone.utc)
        await db.commit()
    return {"message": "Session revoked"}


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
