"""Tests for obligation-reminder dispatch (spec obligations / C3).

Verifies the scheduled reminder path now resolves real org-member
recipients (instead of the hardcoded demo@contractos.lk stub), records
ObligationReminder audit rows, and is idempotent across cycles.
"""
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import NotificationPreference
from app.models.obligation import Obligation, ObligationReminder
from app.models.rbac import OrganizationMember
from app.models.user import User
from app.services.obligation_reminder_service import (
    dispatch_obligation_reminders,
)


async def _mk_obligation(
    db: AsyncSession, agreement, *, due_offset_days, status="upcoming", owner="Test Corp"
) -> Obligation:
    obligation = Obligation(
        agreement_id=agreement.id,
        owner_party=owner,
        description="Submit compliance report",
        obligation_type="reporting",
        due_date=date.today() + timedelta(days=due_offset_days),
        status=status,
    )
    db.add(obligation)
    await db.flush()
    return obligation


class TestObligationReminderDispatch:
    async def test_dispatches_to_active_org_members(
        self, db_session, test_org, test_user, test_agreement
    ):
        # test_user is an active member of test_org via the test_org fixture.
        obligation = await _mk_obligation(db_session, test_agreement, due_offset_days=3)
        await db_session.commit()

        result = await dispatch_obligation_reminders(db_session)

        assert result["obligations_checked"] == 1
        assert result["sent"] == 1
        assert result["skipped"] == 0

        reminders = (
            await db_session.execute(
                select(ObligationReminder).where(
                    ObligationReminder.obligation_id == obligation.id
                )
            )
        ).scalars().all()
        assert len(reminders) == 1
        assert reminders[0].status == "sent"
        assert reminders[0].reminder_type == "advance"

    async def test_idempotent_across_cycles(
        self, db_session, test_org, test_user, test_agreement
    ):
        obligation = await _mk_obligation(db_session, test_agreement, due_offset_days=5)
        await db_session.commit()

        first = await dispatch_obligation_reminders(db_session)
        second = await dispatch_obligation_reminders(db_session)

        assert first["sent"] == 1
        assert second["sent"] == 0
        assert second["skipped"] == 1

        reminders = (
            await db_session.execute(
                select(ObligationReminder).where(
                    ObligationReminder.obligation_id == obligation.id
                )
            )
        ).scalars().all()
        assert len(reminders) == 1

    async def test_respects_opt_out_preferences(
        self, db_session, test_org, test_user, test_agreement
    ):
        pref = NotificationPreference(
            user_id=test_user.id,
            organization_id=test_org.id,
            email_obligation_reminder=False,
        )
        db_session.add(pref)
        obligation = await _mk_obligation(db_session, test_agreement, due_offset_days=3)
        await db_session.commit()

        result = await dispatch_obligation_reminders(db_session)

        # Opted-out member is dropped → nothing delivered, reminder marked failed.
        assert result["sent"] == 0
        reminders = (
            await db_session.execute(
                select(ObligationReminder).where(
                    ObligationReminder.obligation_id == obligation.id
                )
            )
        ).scalars().all()
        assert reminders[0].status == "failed"

    async def test_ignores_obligations_outside_window(
        self, db_session, test_org, test_user, test_agreement
    ):
        obligation = await _mk_obligation(db_session, test_agreement, due_offset_days=30)
        await db_session.commit()

        result = await dispatch_obligation_reminders(db_session)

        assert result["obligations_checked"] == 0
        assert result["sent"] == 0
        assert result["skipped"] == 0

    async def test_skips_already_completed_obligations(
        self, db_session, test_org, test_user, test_agreement
    ):
        obligation = await _mk_obligation(
            db_session, test_agreement, due_offset_days=2, status="completed"
        )
        await db_session.commit()

        result = await dispatch_obligation_reminders(db_session)

        assert result["obligations_checked"] == 0
        assert result["sent"] == 0