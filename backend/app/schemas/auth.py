from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr


class UserRegister(BaseModel):
    email: EmailStr
    name: str
    password: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str
    mfa_code: str | None = None


class MFAVerify(BaseModel):
    code: str


class MFASetupResponse(BaseModel):
    secret: str
    provisioning_uri: str


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
    email_verified_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
