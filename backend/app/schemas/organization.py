from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class OrganizationCreate(BaseModel):
    name: str
    country: str = "LK"
    timezone: str = "Asia/Colombo"


class OrganizationResponse(BaseModel):
    id: UUID
    name: str
    slug: str
    country: str
    timezone: str
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class OrganizationMemberResponse(BaseModel):
    id: UUID
    user_id: UUID
    organization_id: UUID
    role_id: UUID
    status: str

    model_config = {"from_attributes": True}


class MembershipOptionResponse(BaseModel):
    """One organization a user may switch into.

    Returned by ``GET /organizations/me/memberships`` so a client holding
    several memberships can choose which tenant subsequent requests act in.
    ``role_id`` is exposed instead of a role name because the role row is
    itself RLS-protected by the tenant being resolved.
    """

    organization_id: UUID
    name: str
    slug: str
    role_id: UUID
