import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TemplateVariableBase(BaseModel):
    key: str
    label: str
    var_type: str = "text"
    required: bool = True
    default_value: str | None = None
    options: list | None = None
    description: str | None = None
    sort_order: int = 0


class TemplateVariableCreate(TemplateVariableBase):
    pass


class TemplateVariableOut(TemplateVariableBase):
    id: uuid.UUID
    template_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class TemplateVersionBase(BaseModel):
    version_number: int
    content: str
    content_hash: str | None = None
    variables_snapshot: dict | None = None
    clauses_snapshot: list | None = None
    rules_snapshot: list | None = None
    status: str = "draft"
    change_notes: str | None = None


class TemplateVersionCreate(TemplateVersionBase):
    pass


class TemplateVersionOut(TemplateVersionBase):
    id: uuid.UUID
    template_id: uuid.UUID
    created_by: uuid.UUID | None = None
    locked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class TemplateBase(BaseModel):
    name: str
    description: str | None = None
    jurisdiction: str | None = None
    language: str | None = None
    status: str = "draft"
    is_system: bool = False
    agreement_type_id: uuid.UUID | None = None


class TemplateCreate(TemplateBase):
    variables: list[TemplateVariableCreate] = []


class TemplateUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    jurisdiction: str | None = None
    language: str | None = None
    status: str | None = None
    is_system: bool | None = None
    agreement_type_id: uuid.UUID | None = None


class TemplateOut(TemplateBase):
    id: uuid.UUID
    organization_id: uuid.UUID | None = None
    created_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime
    variables: list[TemplateVariableOut] = []

    class Config:
        from_attributes = True
