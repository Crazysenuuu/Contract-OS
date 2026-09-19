"""Rules Engine API endpoints (spec 24.2).

Allows admins to create, list, evaluate, and delete dynamic DOA rules
stored as JSON condition trees on ApprovalDefinition rows.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.currency_service import default_currency
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.approval import ApprovalDefinition
from app.models.user import User
from app.services.rules_engine import (
    create_rule_definition,
    evaluate_doa_rules,
    list_rule_definitions,
)

router = APIRouter(prefix="/rules-engine", tags=["Rules Engine"])


class StageIn(BaseModel):
    name: str
    required_role: str
    execution_mode: str = "sequential"
    require_all_approvers: bool = True


class RuleDefinitionCreate(BaseModel):
    name: str
    description: str | None = None
    rules: dict  # {"conditions": {"all": [...]}, "actions": [...]}
    stages: list[StageIn]


class EvaluateRequest(BaseModel):
    agreement_value: float
    agreement_type: str | None = None
    currency: str = Field(default_factory=default_currency)
    risk_score: float | None = None
    counterparty_country: str | None = None
    extra_context: dict | None = None


@router.get("")
async def list_rules(
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """List all active rules-based approval definitions."""
    return await list_rule_definitions(db, org_id)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_rule(
    data: RuleDefinitionCreate,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Create a new rules-based approval definition."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    definition = await create_rule_definition(
        db,
        organization_id=org_id,
        name=data.name,
        description=data.description,
        rules=data.rules,
        stages=[s.model_dump() for s in data.stages],
    )
    await db.commit()
    return {"id": str(definition.id), "name": definition.name, "rules": definition.rules}


@router.delete("/{definition_id}", status_code=status.HTTP_200_OK)
async def delete_rule(
    definition_id: UUID,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Deactivate a rules-based approval definition (soft delete).

    Definitions are referenced by approval records, so they are deactivated
    rather than removed — historical routing stays auditable.
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    result = await db.execute(
        select(ApprovalDefinition).where(
            ApprovalDefinition.id == definition_id,
            ApprovalDefinition.organization_id == org_id,
        )
    )
    definition = result.scalar_one_or_none()
    if definition is None:
        raise HTTPException(status_code=404, detail="Rule definition not found")
    definition.is_active = False
    await db.commit()
    return {"id": str(definition.id), "is_active": False}


@router.post("/evaluate")
async def evaluate_rules(
    data: EvaluateRequest,
    current_user: User = Depends(get_current_user),
    org_id: UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Evaluate DOA rules for a given agreement context (dry-run)."""
    result = await evaluate_doa_rules(
        db,
        organization_id=org_id,
        agreement_value=data.agreement_value,
        agreement_type=data.agreement_type,
        currency=data.currency,
        risk_score=data.risk_score,
        counterparty_country=data.counterparty_country,
        extra_context=data.extra_context,
    )
    return result
