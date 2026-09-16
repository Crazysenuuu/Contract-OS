from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class LegalEntityCreate(BaseModel):
    legal_name: str
    registration_number: str | None = None
    entity_type: str | None = None
    country: str
    registered_address: str | None = None
    tax_identifier: str | None = None


class LegalEntityResponse(BaseModel):
    id: UUID
    organization_id: UUID
    legal_name: str
    registration_number: str | None
    entity_type: str | None
    country: str
    registered_address: str | None
    tax_identifier: str | None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
