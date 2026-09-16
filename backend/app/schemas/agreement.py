from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AgreementCreate(BaseModel):
    title: str
    agreement_type_id: UUID
    governing_law: str | None = None
    effective_date: date | None = None
    parent_agreement_id: UUID | None = None
    data: dict | None = None


class AgreementUpdate(BaseModel):
    title: str | None = None
    governing_law: str | None = None
    effective_date: date | None = None
    expiry_date: date | None = None


class AgreementResponse(BaseModel):
    id: UUID
    organization_id: UUID
    agreement_type_id: UUID
    agreement_type_version: int | None
    parent_agreement_id: UUID | None
    title: str
    status: str
    governing_law: str | None
    effective_date: date | None
    execution_date: date | None
    expiry_date: date | None
    created_by: UUID
    data: dict
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AgreementVersionResponse(BaseModel):
    id: UUID
    agreement_id: UUID
    version_number: int
    content: str
    content_hash: str
    status: str
    created_by: UUID
    locked_at: date | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AgreementTypeResponse(BaseModel):
    id: UUID
    key: str
    name: str
    description: str | None
    category: str
    template_key: str | None
    status: str
    version: int
    # Renamed from `schema` to avoid conflicting with Pydantic's BaseModel.schema()
    # classmethod. The alias ensures ORM serialisation and JSON output still use "schema".
    json_schema: dict | None = Field(default=None, alias="schema")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
