"""Clause applicability engine (spec 1.8.10).

Combines approved-version selection, jurisdiction bindings and data-driven
conditions into a single decision: should this clause be included?
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.clause import ClauseCondition, ClauseJurisdiction, ClauseVersion
from app.services.clause_condition_engine import ClauseConditionEngine
from app.services.clause_selector import ClauseSelector


@dataclass
class ApplicableClause:
    clause_version: ClauseVersion
    reason: str


class ClauseApplicabilityService:
    def __init__(self):
        self.selector = ClauseSelector()
        self.condition_engine = ClauseConditionEngine()

    async def resolve_clause(
        self,
        db: AsyncSession,
        *,
        clause_id,
        jurisdiction_id=None,
        agreement_data: dict,
        effective_at: datetime | None = None,
    ) -> ApplicableClause | None:
        effective_at = effective_at or datetime.now(timezone.utc)

        clause_version = await self.selector.select_current_approved_version(
            db, clause_id=clause_id, effective_at=effective_at
        )
        if clause_version is None:
            return None

        if jurisdiction_id is not None:
            jurisdiction_result = await db.execute(
                select(ClauseJurisdiction).where(
                    ClauseJurisdiction.clause_version_id == clause_version.id,
                    ClauseJurisdiction.jurisdiction_id == jurisdiction_id,
                )
            )
            jurisdiction_binding = jurisdiction_result.scalar_one_or_none()
            # An explicit 'not applicable' binding excludes the clause; no
            # binding means the clause is jurisdiction-neutral.
            if jurisdiction_binding is not None and not jurisdiction_binding.applicable:
                return None

        condition_result = await db.execute(
            select(ClauseCondition)
            .where(ClauseCondition.clause_version_id == clause_version.id)
            .order_by(ClauseCondition.display_order)
        )
        conditions = condition_result.scalars().all()

        for condition in conditions:
            result = self.condition_engine.evaluate(condition.condition, agreement_data)
            if not result.matched:
                return None

        return ApplicableClause(
            clause_version=clause_version,
            reason="Applicable approved clause version",
        )
