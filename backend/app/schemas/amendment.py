from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel


class AmendmentChange(BaseModel):
    id: UUID | None = None
    section_key: str
    change_type: str
    old_text: str | None = None
    new_text: str
    structured_delta: dict | None = None

    model_config = {"from_attributes": True}


class AmendmentResponse(BaseModel):
    id: UUID
    agreement_id: UUID
    amendment_number: int
    title: str
    description: str | None
    reason: str | None
    status: str
    version_number: int
    effective_date: date | None
    base_version_number: int | None
    current_terms_snapshot: dict | None
    proposed_by: UUID | None
    approved_by: UUID | None
    approved_at: datetime | None
    created_at: datetime
    changes: list[AmendmentChange] | None = None

    model_config = {"from_attributes": True}