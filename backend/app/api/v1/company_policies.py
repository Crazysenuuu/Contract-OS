"""Company policy engine API endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List
from pydantic import BaseModel

from app.core.database import get_db
from app.dependencies.auth import get_current_user, get_user_org_ids
from app.models.user import User
from app.models.agreement import Agreement
from app.services.company_policy_engine import CompanyPolicyEngine

router = APIRouter(prefix="/company-policies", tags=["Company Policies"])


async def _get_org_agreement(
    db: AsyncSession,
    current_user: User,
    agreement_id: str,
) -> Agreement:
    org_ids = await get_user_org_ids(db, current_user.id)
    result = await db.execute(
        select(Agreement).where(
            Agreement.id == agreement_id,
            Agreement.organization_id.in_(org_ids) if org_ids else Agreement.id.is_(None),
        )
    )
    agreement = result.scalar_one_or_none()
    if agreement is None:
        raise HTTPException(status_code=404, detail="Agreement not found")
    return agreement


class EvaluateAgreementRequest(BaseModel):
    agreement_id: str
    agreement_data: Optional[dict] = None


class AddRuleRequest(BaseModel):
    rule_id: str
    name: str
    description: str
    condition_key: str
    action: str = "require_approval"
    approval_roles: Optional[List[str]] = None
    severity: str = "medium"


@router.get("/rules")
async def list_rules(
    current_user: User = Depends(get_current_user)
):
    """List all company policy rules."""
    engine = CompanyPolicyEngine(None)
    return {"rules": engine.get_all_rules()}


@router.post("/rules")
async def add_rule(
    request: AddRuleRequest,
    current_user: User = Depends(get_current_user)
):
    """Add a custom policy rule."""
    engine = CompanyPolicyEngine(None)
    engine.add_custom_rule(
        rule_id=request.rule_id,
        name=request.name,
        description=request.description,
        condition_key=request.condition_key,
        action=request.action,
        approval_roles=request.approval_roles,
        severity=request.severity,
    )
    return {"status": "added", "rule_id": request.rule_id}


@router.delete("/rules/{rule_id}")
async def delete_rule(
    rule_id: str,
    current_user: User = Depends(get_current_user)
):
    """Delete a policy rule."""
    engine = CompanyPolicyEngine(None)
    removed = engine.remove_rule(rule_id)
    if removed:
        return {"status": "deleted", "rule_id": rule_id}
    else:
        raise HTTPException(status_code=404, detail="Rule not found")


@router.get("/summary")
async def get_policy_summary(
    current_user: User = Depends(get_current_user)
):
    """Get policy summary."""
    engine = CompanyPolicyEngine(None)
    return engine.get_policy_summary()


@router.post("/evaluate")
async def evaluate_agreement(
    request: EvaluateAgreementRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Evaluate an agreement against company policies."""
    # Get the org-scoped agreement (async session)
    agreement = await _get_org_agreement(db, current_user, request.agreement_id)

    engine = CompanyPolicyEngine(db)
    result = engine.evaluate_agreement(agreement, request.agreement_data)
    return result


@router.get("/checks")
async def get_policy_checks(
    agreement_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get policy checks for an agreement."""
    agreement = await _get_org_agreement(db, current_user, agreement_id)

    engine = CompanyPolicyEngine(db)
    result = engine.evaluate_agreement(agreement)

    return {
        "agreement_id": agreement_id,
        "compliance_score": result["compliance_score"],
        "warnings": result["warnings"],
        "required_approvals": result["required_approvals"],
        "summary": {
            "passed": result["rules_evaluated"] - result["rules_triggered"],
            "failed": result["rules_triggered"],
            "warnings": len(result["warnings"]),
        },
    }
