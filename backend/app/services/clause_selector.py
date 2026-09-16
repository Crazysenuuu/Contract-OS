"""Clause version selection (spec 1.8.8).

Type-agnostic selection of the applicable approved clause version. There is
deliberately no 'if clause == NDA' logic anywhere in here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.clause import (
    AgreementTypeClauseBinding,
    Clause,
    ClauseCondition,
    ClauseVersion,
)
from app.services.clause_condition_engine import ClauseConditionEngine

logger = logging.getLogger(__name__)
_engine = ClauseConditionEngine()


@dataclass
class SelectedClause:
    """A resolved clause version ready for agreement assembly."""

    binding: AgreementTypeClauseBinding
    clause: Clause
    version: ClauseVersion
    display_order: int


class ClauseSelector:
    """Selects clause versions by status and effective window."""

    async def select_current_approved_version(
        self,
        db: AsyncSession,
        *,
        clause_id,
        effective_at: datetime | None = None,
    ) -> ClauseVersion | None:
        effective_at = effective_at or datetime.now(timezone.utc)

        result = await db.execute(
            select(ClauseVersion)
            .where(
                ClauseVersion.clause_id == clause_id,
                ClauseVersion.status == "approved",
                ClauseVersion.effective_from.is_not(None),
                ClauseVersion.effective_from <= effective_at,
            )
            .where(
                (ClauseVersion.effective_until.is_(None))
                | (ClauseVersion.effective_until > effective_at)
            )
            .order_by(ClauseVersion.version_number.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def select_by_key(
        self,
        db: AsyncSession,
        *,
        clause_key: str,
        organization_id=None,
        effective_at: datetime | None = None,
    ) -> ClauseVersion | None:
        """Resolve a clause by its stable key to the current approved version."""
        effective_at = effective_at or datetime.now(timezone.utc)
        query = (
            select(ClauseVersion)
            .join(Clause, Clause.id == ClauseVersion.clause_id)
            .where(
                Clause.key == clause_key,
                ClauseVersion.status == "approved",
                ClauseVersion.effective_from.is_not(None),
                ClauseVersion.effective_from <= effective_at,
            )
            .where(
                (ClauseVersion.effective_until.is_(None))
                | (ClauseVersion.effective_until > effective_at)
            )
            .order_by(ClauseVersion.version_number.desc())
            .limit(1)
        )
        if organization_id is not None:
            query = query.where(Clause.organization_id == organization_id)
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def select_clauses_for_agreement(
        self,
        db: AsyncSession,
        *,
        agreement_type_id,
        agreement_data: dict[str, Any],
        organization_id=None,
        effective_at: datetime | None = None,
    ) -> list[SelectedClause]:
        """Select all applicable clause versions for an agreement (spec 1.8.9).

        Steps:
        1. Load all AgreementTypeClauseBindings for the given agreement type,
           optionally scoped to the organization.
        2. For each binding, resolve the current approved ClauseVersion
           (respecting effective_from / effective_until).
        3. Evaluate every ClauseCondition attached to that version against
           *agreement_data* using the closed-operator ClauseConditionEngine.
           Versions whose conditions do not ALL pass are excluded.
        4. Return the surviving versions sorted by display_order, preserving
           required-clause semantics (required clauses that fail conditions are
           logged as warnings rather than silently dropped).

        ``agreement_data`` is a flat-ish dict that the caller populates from
        the agreement, its parties, jurisdiction, etc.  Example keys:

            {
                "agreement.agreement_type_key": "NDA",
                "agreement.governing_law": "SL",
                "agreement.value": 50000,
                "party_a.is_consumer": False,
                "party_b.is_consumer": True,
            }
        """
        effective_at = effective_at or datetime.now(timezone.utc)

        # 1. Load bindings
        binding_query = select(AgreementTypeClauseBinding).where(
            AgreementTypeClauseBinding.agreement_type_id == agreement_type_id
        )
        if organization_id is not None:
            binding_query = binding_query.where(
                (AgreementTypeClauseBinding.organization_id == organization_id)
                | (AgreementTypeClauseBinding.organization_id.is_(None))
            )
        bindings_result = await db.execute(binding_query)
        bindings: list[AgreementTypeClauseBinding] = list(
            bindings_result.scalars().all()
        )

        if not bindings:
            logger.debug(
                "select_clauses_for_agreement: no bindings for agreement_type_id=%s",
                agreement_type_id,
            )
            return []

        selected: list[SelectedClause] = []

        for binding in sorted(bindings, key=lambda b: b.display_order):
            # 2. Resolve current approved version
            version = await self.select_current_approved_version(
                db,
                clause_id=binding.clause_id,
                effective_at=effective_at,
            )
            if version is None:
                if binding.required:
                    logger.warning(
                        "Required clause %s has no approved version — skipping",
                        binding.clause_id,
                    )
                continue

            # Eagerly load conditions if not already loaded
            if not version.conditions:
                cond_result = await db.execute(
                    select(ClauseCondition)
                    .where(ClauseCondition.clause_version_id == version.id)
                    .order_by(ClauseCondition.display_order)
                )
                conditions: list[ClauseCondition] = list(cond_result.scalars().all())
            else:
                conditions = list(version.conditions)

            # 3. Evaluate conditions
            all_passed = True
            for cond_row in conditions:
                result = _engine.evaluate(cond_row.condition, agreement_data)
                if not result.matched:
                    all_passed = False
                    logger.debug(
                        "Clause %s (version %s) excluded: %s",
                        binding.clause_id,
                        version.id,
                        result.reason,
                    )
                    break

            if not all_passed:
                if binding.required:
                    logger.warning(
                        "Required clause %s failed condition check — omitting from assembly",
                        binding.clause_id,
                    )
                continue

            # Load parent Clause for metadata
            clause_result = await db.execute(
                select(Clause).where(Clause.id == binding.clause_id)
            )
            clause = clause_result.scalar_one()

            selected.append(
                SelectedClause(
                    binding=binding,
                    clause=clause,
                    version=version,
                    display_order=binding.display_order,
                )
            )

        return selected

