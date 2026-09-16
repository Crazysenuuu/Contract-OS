"""Dynamic Delegation of Authority (DOA) approval matrix (spec 24.2).

Replaces the hardcoded value thresholds in SignatureAuthorityEngine with a
database-configurable matrix: organizations define ApprovalDefinition rows
(value bands) with ApprovalStage steps (sequential or parallel fan-out).
When no DB rules are configured, falls back to the previous hardcoded
thresholds so existing behavior is preserved.
"""
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.approval import ApprovalDefinition, ApprovalStage

# Fallback matrix (LKR) used when the organization has no DB-configured rules
FALLBACK_MATRIX = [
    {
        "min_value": 10_000_000,
        "max_value": None,
        "name": "Executive approval",
        "stages": [
            {"role": "ceo", "execution_mode": "parallel", "require_all": True},
            {"role": "cfo", "execution_mode": "parallel", "require_all": True},
            {"role": "legal", "execution_mode": "parallel", "require_all": True},
        ],
    },
    {
        "min_value": 5_000_000,
        "max_value": 10_000_000,
        "name": "Director approval",
        "stages": [
            {"role": "director", "execution_mode": "parallel", "require_all": True},
            {"role": "finance", "execution_mode": "parallel", "require_all": True},
        ],
    },
    {
        "min_value": 1_000_000,
        "max_value": 5_000_000,
        "name": "Manager approval",
        "stages": [
            {"role": "manager", "execution_mode": "sequential", "require_all": True},
        ],
    },
]


class DoaMatrixError(Exception):
    pass


async def _convert_to_base(amount: float, currency: str) -> float:
    """Convert to LKR using the same simplified rates as the authority engine."""
    rates = {
        "LKR": 1.0,
        "USD": 300.0,
        "EUR": 330.0,
        "GBP": 380.0,
        "SGD": 225.0,
        "INR": 3.6,
    }
    return amount * rates.get(currency, 1.0)


async def resolve_doa_matrix(
    db: AsyncSession,
    organization_id: Optional[UUID],
    agreement_value: float,
    currency: str = "LKR",
    agreement_type: Optional[str] = None,
) -> dict[str, Any]:
    """Resolve the applicable approval matrix for an agreement value.

    Returns ``{"source": "database"|"fallback", "definition": {...},
    "required_approvals": [{role, execution_mode, required}], "total": n}``.
    """
    # DB matrices are authored in the organization's own currency, so their
    # value bands compare against the raw agreement value. The built-in
    # fallback matrix is denominated in LKR, so it converts first.
    base_value = await _convert_to_base(agreement_value, currency)

    definitions = []
    if organization_id is not None:
        result = await db.execute(
            select(ApprovalDefinition)
            .options(selectinload(ApprovalDefinition.stages))
            .where(
                and_(
                    ApprovalDefinition.organization_id == organization_id,
                    ApprovalDefinition.is_active.is_(True),
                )
            )
            .order_by(ApprovalDefinition.min_value.desc())
        )
        definitions = list(result.scalars().all())

    for definition in definitions:
        min_ok = definition.min_value is None or agreement_value >= float(definition.min_value)
        max_ok = definition.max_value is None or agreement_value <= float(definition.max_value)
        if min_ok and max_ok:
            stages = sorted(definition.stages, key=lambda s: s.order)
            required = [
                {
                    "role": stage.required_role,
                    "execution_mode": stage.execution_mode,
                    "required": True,
                    "require_all_approvers": stage.require_all_approvers,
                    "stage_id": str(stage.id),
                }
                for stage in stages
                if stage.required_role
            ]
            return {
                "source": "database",
                "definition_id": str(definition.id),
                "name": definition.name,
                "required_approvals": required,
                "total": len(required),
            }

    # Fallback: seeded LKR thresholds
    for tier in FALLBACK_MATRIX:
        if base_value >= tier["min_value"] and (
            tier["max_value"] is None or base_value <= tier["max_value"]
        ):
            return {
                "source": "fallback",
                "definition_id": None,
                "name": tier["name"],
                "required_approvals": [
                    {
                        "role": stage["role"],
                        "execution_mode": stage["execution_mode"],
                        "required": True,
                        "require_all_approvers": stage["require_all"],
                        "stage_id": None,
                    }
                    for stage in tier["stages"]
                ],
                "total": len(tier["stages"]),
            }

    return {
        "source": "fallback",
        "definition_id": None,
        "name": "No approval required",
        "required_approvals": [],
        "total": 0,
    }


async def list_matrices(db: AsyncSession, organization_id: UUID) -> list[dict[str, Any]]:
    result = await db.execute(
        select(ApprovalDefinition)
        .options(selectinload(ApprovalDefinition.stages))
        .where(
            and_(
                ApprovalDefinition.organization_id == organization_id,
                ApprovalDefinition.is_active.is_(True),
            )
        )
        .order_by(ApprovalDefinition.min_value.desc())
    )
    matrices = []
    for definition in result.scalars().all():
        stages = sorted(definition.stages, key=lambda s: s.order)
        matrices.append(
            {
                "id": str(definition.id),
                "name": definition.name,
                "description": definition.description,
                "min_value": float(definition.min_value) if definition.min_value is not None else None,
                "max_value": float(definition.max_value) if definition.max_value is not None else None,
                "stages": [
                    {
                        "id": str(stage.id),
                        "name": stage.name,
                        "order": stage.order,
                        "required_role": stage.required_role,
                        "execution_mode": stage.execution_mode,
                        "require_all_approvers": stage.require_all_approvers,
                    }
                    for stage in stages
                ],
            }
        )
    return matrices


async def create_matrix(
    db: AsyncSession,
    organization_id: UUID,
    *,
    name: str,
    description: Optional[str] = None,
    min_value: Optional[float] = None,
    max_value: Optional[float] = None,
    stages: list[dict[str, Any]],
) -> ApprovalDefinition:
    definition = ApprovalDefinition(
        organization_id=organization_id,
        name=name,
        description=description,
        min_value=min_value,
        max_value=max_value,
        is_active=True,
    )
    db.add(definition)
    await db.flush()
    for idx, stage in enumerate(stages):
        mode = stage.get("execution_mode", "sequential")
        if mode not in ("sequential", "parallel"):
            raise DoaMatrixError("execution_mode must be 'sequential' or 'parallel'")
        db.add(
            ApprovalStage(
                definition_id=definition.id,
                name=stage.get("name") or stage.get("required_role") or f"Stage {idx + 1}",
                order=idx,
                required_role=stage.get("required_role"),
                execution_mode=mode,
                require_all_approvers=bool(stage.get("require_all_approvers", True)),
            )
        )
    await db.flush()
    await db.refresh(definition)
    return definition