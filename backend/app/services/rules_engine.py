"""Dynamic Rules Engine for Delegation of Authority (DOA) approval routing (spec 24.2).

Integrates the ``business-rules`` library so organisations can author
declarative condition/action JSON rules for approval routing — no code changes
required to add new tiers, agreement-type gates, or risk-based escalations.

Rule format (stored in ApprovalDefinition.rules JSON column)::

    {
      "conditions": {
        "all": [
          {"name": "agreement_value", "operator": "greater_than", "value": 1000000},
          {"name": "agreement_type",  "operator": "equal_to",     "value": "enterprise_saas"}
        ]
      },
      "actions": [
        {"name": "require_approval", "params": {"definition_id": "<uuid>"}}
      ]
    }

The engine evaluates rules in priority order and returns the first matching
ApprovalDefinition. Falls back to the existing doa_service threshold matrix
when no rule matches.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.approval import ApprovalDefinition

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Variable / condition helpers (mirrors business-rules operator contract)
# ---------------------------------------------------------------------------

NUMERIC_OPS = {
    "greater_than":          lambda a, b: a > b,
    "greater_than_or_equal": lambda a, b: a >= b,
    "less_than":             lambda a, b: a < b,
    "less_than_or_equal":    lambda a, b: a <= b,
    "equal_to":              lambda a, b: a == b,
    "not_equal_to":          lambda a, b: a != b,
}

STRING_OPS = {
    "equal_to":              lambda a, b: str(a).lower() == str(b).lower(),
    "not_equal_to":          lambda a, b: str(a).lower() != str(b).lower(),
    "contains":              lambda a, b: str(b).lower() in str(a).lower(),
    "not_contains":          lambda a, b: str(b).lower() not in str(a).lower(),
    "starts_with":           lambda a, b: str(a).lower().startswith(str(b).lower()),
    "ends_with":             lambda a, b: str(a).lower().endswith(str(b).lower()),
    "is_in":                 lambda a, b: str(a).lower() in [str(x).lower() for x in b],
    "not_in":                lambda a, b: str(a).lower() not in [str(x).lower() for x in b],
}

BOOL_OPS = {
    "is_true":  lambda a, _: bool(a),
    "is_false": lambda a, _: not bool(a),
}

_ALL_OPS: dict[str, Any] = {**NUMERIC_OPS, **STRING_OPS, **BOOL_OPS}


def _evaluate_condition(condition: dict, context: dict) -> bool:
    """Evaluate a single condition dict against a context dict."""
    name: str = condition.get("name", "")
    operator: str = condition.get("operator", "equal_to")
    value = condition.get("value")

    actual = context.get(name)
    if actual is None:
        return False

    op_fn = _ALL_OPS.get(operator)
    if op_fn is None:
        _log.warning("Unknown rule operator: %s", operator)
        return False

    try:
        return bool(op_fn(actual, value))
    except Exception:
        return False


def _evaluate_conditions(conditions: dict, context: dict) -> bool:
    """Evaluate a conditions block (supports 'all' / 'any' / 'none' groups)."""
    if "all" in conditions:
        return all(_evaluate_conditions(c, context) if isinstance(c, dict) and ("all" in c or "any" in c or "none" in c)
                   else _evaluate_condition(c, context)
                   for c in conditions["all"])
    if "any" in conditions:
        return any(_evaluate_conditions(c, context) if isinstance(c, dict) and ("all" in c or "any" in c or "none" in c)
                   else _evaluate_condition(c, context)
                   for c in conditions["any"])
    if "none" in conditions:
        return not any(_evaluate_conditions(c, context) if isinstance(c, dict) and ("all" in c or "any" in c or "none" in c)
                       else _evaluate_condition(c, context)
                       for c in conditions["none"])
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def evaluate_doa_rules(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    agreement_value: float,
    agreement_type: str | None = None,
    currency: str = "LKR",
    risk_score: float | None = None,
    counterparty_country: str | None = None,
    extra_context: dict | None = None,
) -> dict[str, Any]:
    """Evaluate all org rule-based definitions and return the first match.

    Returns a result dict::

        {
            "source": "rules_engine" | "fallback",
            "definition_id": "<uuid>" | None,
            "name": "<workflow name>",
            "required_approvals": [...],
            "total": n,
            "matched_rule": {...} | None,
        }
    """
    # Build evaluation context
    context: dict[str, Any] = {
        "agreement_value": agreement_value,
        "agreement_type": agreement_type or "",
        "currency": currency,
        "risk_score": risk_score or 0.0,
        "counterparty_country": counterparty_country or "",
        **(extra_context or {}),
    }

    # Load all active definitions that have a rules payload
    result = await db.execute(
        select(ApprovalDefinition)
        .options(selectinload(ApprovalDefinition.stages))
        .where(
            and_(
                ApprovalDefinition.organization_id == organization_id,
                ApprovalDefinition.is_active.is_(True),
                ApprovalDefinition.rules.isnot(None),
            )
        )
        .order_by(ApprovalDefinition.created_at.desc())
    )
    definitions = list(result.scalars().all())

    for definition in definitions:
        rules_payload: dict = definition.rules or {}
        conditions = rules_payload.get("conditions", {})

        if conditions and _evaluate_conditions(conditions, context):
            stages = sorted(definition.stages, key=lambda s: s.order)
            required = [
                {
                    "role": stage.required_role,
                    "execution_mode": stage.execution_mode,
                    "required": True,
                    "require_all_approvers": stage.require_all_approvers,
                    "stage_id": str(stage.id),
                    "stage_name": stage.name,
                }
                for stage in stages
                if stage.required_role
            ]
            _log.info(
                "Rules engine matched definition '%s' for org %s (value=%.2f, type=%s)",
                definition.name, organization_id, agreement_value, agreement_type,
            )
            return {
                "source": "rules_engine",
                "definition_id": str(definition.id),
                "name": definition.name,
                "required_approvals": required,
                "total": len(required),
                "matched_rule": conditions,
            }

    # No rule matched → delegate to threshold-based DOA service
    from app.services.doa_service import resolve_doa_matrix

    fallback = await resolve_doa_matrix(
        db,
        organization_id=organization_id,
        agreement_value=agreement_value,
        currency=currency,
        agreement_type=agreement_type,
    )
    return {**fallback, "matched_rule": None}


async def create_rule_definition(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    name: str,
    description: str | None = None,
    rules: dict,
    stages: list[dict],
) -> ApprovalDefinition:
    """Create a rules-based ApprovalDefinition.

    ``rules`` is the condition/action JSON as described above.
    ``stages`` is the ordered list of approval stages to trigger.
    """
    from app.services.doa_service import create_matrix
    from app.models.approval import ApprovalDefinition as AD
    from sqlalchemy import JSON

    definition = await create_matrix(
        db,
        organization_id=organization_id,
        name=name,
        description=description,
        stages=stages,
    )
    definition.rules = rules
    await db.flush()
    return definition


async def list_rule_definitions(
    db: AsyncSession,
    organization_id: uuid.UUID,
) -> list[dict]:
    """Return all rule-based definitions with their JSON rules payload."""
    result = await db.execute(
        select(ApprovalDefinition)
        .options(selectinload(ApprovalDefinition.stages))
        .where(
            and_(
                ApprovalDefinition.organization_id == organization_id,
                ApprovalDefinition.is_active.is_(True),
                ApprovalDefinition.rules.isnot(None),
            )
        )
        .order_by(ApprovalDefinition.created_at.desc())
    )
    out = []
    for d in result.scalars().all():
        out.append({
            "id": str(d.id),
            "name": d.name,
            "description": d.description,
            "rules": d.rules,
            "stages": [
                {
                    "id": str(s.id),
                    "name": s.name,
                    "order": s.order,
                    "required_role": s.required_role,
                    "execution_mode": s.execution_mode,
                    "require_all_approvers": s.require_all_approvers,
                }
                for s in sorted(d.stages, key=lambda s: s.order)
            ],
        })
    return out
