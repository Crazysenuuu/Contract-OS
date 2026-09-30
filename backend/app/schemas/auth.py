from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, field_validator


class UserRegister(BaseModel):
    email: EmailStr
    name: str
    password: str
    # COPPA age gate: date of birth is required at signup. The raw DOB is NOT
    # persisted anywhere — only the derived boolean is stored on the User row
    # so the product can prove an age check happened without retaining a
    # birth date (data minimization).
    date_of_birth: date

    @field_validator("date_of_birth")
    @classmethod
    def _validate_age(cls, v: date) -> date:
        from datetime import date as _date

        today = _date.today()
        age = (
            today.year
            - v.year
            - ((today.month, today.day) < (v.month, v.day))
        )
        if age < 13:
            raise ValueError(
                "You must be at least 13 years old to create an account"
            )
        if age > 130:
            raise ValueError("Please enter a valid date of birth")
        return v


class UserLogin(BaseModel):
    email: EmailStr
    password: str
    mfa_code: str | None = None


class MFAVerify(BaseModel):
    code: str


class MFASetupResponse(BaseModel):
    secret: str
    provisioning_uri: str


class MFADisable(BaseModel):
    """Both factors are required to turn MFA off (hardened per spec 1.22):
    knowledge of the password alone must not suffice for an attacker with a
    stolen session token, and the TOTP code alone must not suffice for
    someone who briefly held the unlocked device."""

    password: str
    code: str


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class ProfileUpdate(BaseModel):
    name: str | None = None
    phone: str | None = None


class SessionResponse(BaseModel):
    id: UUID
    ip_address: str | None
    user_agent: str | None
    login_at: datetime
    last_seen_at: datetime | None
    logout_at: datetime | None
    status: str

    model_config = {"from_attributes": True}


class EmailVerify(BaseModel):
    token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"
    user_id: UUID


class RefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    id: UUID
    email: str
    name: str
    status: str
    is_admin: bool = False
    mfa_enabled: bool = False
    phone: str | None = None
    email_verified_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
