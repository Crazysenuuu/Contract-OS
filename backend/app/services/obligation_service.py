"""
Obligation Service - Extract, track, and manage post-signing obligations.

Obligations are extracted from executed agreements and tracked until completion.
"""

from datetime import datetime, date
from typing import Optional
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.obligation import Obligation, ObligationReminder


class ObligationService:
    """Manages obligation lifecycle."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def extract_obligations(
        self,
        agreement_id: UUID,
        extracted_by: str = "system",
        method: str = "hybrid",
    ) -> dict:
        """Extract candidate obligations from the current agreement version.

        Delegates to the hybrid extraction engine (spec 1.16.21-23): AI
        extraction plus a deterministic fallback, all results created as
        CANDIDATE obligations with full source traceability, and the run
        recorded for provenance. Returns the run summary.
        """
        from app.services.obligation_extractor import ObligationExtractor

        extractor = ObligationExtractor(self.db)
        return await extractor.extract(agreement_id, method=method)

    async def create_obligation(
        self,
        agreement_id: UUID,
        owner_party: str,
        description: str,
        obligation_type: str,
        amount: Optional[str] = None,
        frequency: Optional[str] = None,
        due_date: Optional[date] = None,
        clause_identifier: Optional[str] = None,
        evidence: Optional[dict] = None,
    ) -> Obligation:
        """Create a new obligation."""
        obligation = Obligation(
            agreement_id=agreement_id,
            owner_party=owner_party,
            description=description,
            obligation_type=obligation_type,
            amount=amount,
            frequency=frequency,
            due_date=due_date,
            clause_identifier=clause_identifier,
            evidence=evidence,
            status="upcoming",
        )
        self.db.add(obligation)
        await self.db.flush()
        return obligation

    async def get_obligation(self, obligation_id: UUID) -> Optional[Obligation]:
        """Get obligation by ID."""
        result = await self.db.execute(
            select(Obligation).where(Obligation.id == obligation_id)
        )
        return result.scalar_one_or_none()

    async def list_agreement_obligations(
        self, agreement_id: UUID, status: Optional[str] = None
    ) -> list[Obligation]:
        """List obligations for an agreement."""
        query = select(Obligation).where(Obligation.agreement_id == agreement_id)
        if status:
            query = query.where(Obligation.status == status)
        query = query.order_by(Obligation.due_date.asc())
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def list_user_obligations(
        self, user_id: UUID, status: Optional[str] = None
    ) -> list[Obligation]:
        """List obligations by owner (party name lookup)."""
        query = select(Obligation)
        if status:
            query = query.where(Obligation.status == status)
        query = query.order_by(Obligation.due_date.asc())
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def update_status(
        self, obligation_id: UUID, new_status: str
    ) -> Optional[Obligation]:
        """Update obligation status."""
        obligation = await self.get_obligation(obligation_id)
        if not obligation:
            return None

        obligation.status = new_status
        await self.db.flush()
        return obligation

    async def mark_overdue(self) -> int:
        """Mark obligations past due date as overdue. Returns count updated."""
        now = date.today()
        result = await self.db.execute(
            select(Obligation).where(
                Obligation.status == "upcoming",
                Obligation.due_date < now,
            )
        )
        obligations = result.scalars().all()
        count = 0
        for ob in obligations:
            ob.status = "overdue"
            count += 1
        if count:
            await self.db.flush()
        return count

    async def get_stats(self, agreement_id: UUID) -> dict:
        """Get obligation statistics for an agreement."""
        obligations = await self.list_agreement_obligations(agreement_id)
        today = date.today()
        from datetime import timedelta
        thirty_days = today + timedelta(days=30)

        return {
            "total": len(obligations),
            "upcoming": sum(1 for o in obligations if o.status == "upcoming"),
            "due": sum(1 for o in obligations if o.status == "due"),
            "completed": sum(1 for o in obligations if o.status == "completed"),
            "overdue": sum(1 for o in obligations if o.status == "overdue"),
            "due_within_30_days": sum(
                1 for o in obligations
                if o.status in ("upcoming", "due")
                and o.due_date and o.due_date <= thirty_days
            ),
        }

    async def create_reminder(
        self,
        obligation_id: UUID,
        reminder_date: datetime,
        reminder_type: str,
    ) -> ObligationReminder:
        """Create a reminder for an obligation."""
        reminder = ObligationReminder(
            obligation_id=obligation_id,
            reminder_date=reminder_date,
            reminder_type=reminder_type,
            status="pending",
        )
        self.db.add(reminder)
        await self.db.flush()
        return reminder
