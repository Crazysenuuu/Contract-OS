"""Tests for the insurance/bonding expiry sweep.

``notify_insurance_expiry`` is the daily safety net for counterparty cover:
it must find insurance-type obligations inside the look-ahead window, email
the owning organisation's active members, record an ``ObligationReminder``
audit row, and not re-notify on the next sweep. Failed deliveries must be
retried rather than silently swallowed.
"""
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import NotificationPreference
from app.models.obligation import Obligation, ObligationReminder
from app.services.insurance_service import notify_insurance_expiry


async def _mk_obligation(
    db: AsyncSession,
    agreement,
    *,
    due_offset_days: int,
    obligation_type: str = "insurance",
    status: str = "upcoming",
) -> Obligation:
    obligation = Obligation(
        agreement_id=agreement.id,
        owner_party="Test Corp",
        description="Maintain public liability insurance",
        obligation_type=obligation_type,
        due_date=date.today() + timedelta(days=due_offset_days),
        status=status,
    )
    db.add(obligation)
    await db.flush()
    return obligation


async def _reminders(db: AsyncSession, obligation: Obligation) -> list[ObligationReminder]:
    return list(
        (
            await db.execute(
                select(ObligationReminder).where(
                    ObligationReminder.obligation_id == obligation.id
                )
            )
        ).scalars().all()
    )


class TestInsuranceExpirySweep:
    async def test_notifies_expiring_cover(
        self, db_session, test_org, test_user, test_agreement
    ):
        obligation = await _mk_obligation(
            db_session, test_agreement, due_offset_days=10
        )
        await db_session.commit()

        result = await notify_insurance_expiry(db_session)

        assert result["obligations_checked"] == 1
        assert result["notified"] == 1
        assert result["skipped"] == 0
        assert result["expiring_soon"] == 1
        assert result["expired"] == 0

        reminders = await _reminders(db_session, obligation)
        assert len(reminders) == 1
        assert reminders[0].status == "sent"
        assert reminders[0].reminder_type == "advance"

    async def test_flags_expired_cover(
        self, db_session, test_org, test_user, test_agreement
    ):
        obligation = await _mk_obligation(
            db_session, test_agreement, due_offset_days=-1
        )
        await db_session.commit()

        result = await notify_insurance_expiry(db_session)

        assert result["notified"] == 1
        assert result["expired"] == 1
        assert result["expiring_soon"] == 0
        reminders = await _reminders(db_session, obligation)
        assert reminders[0].reminder_type == "overdue"

    async def test_due_today_is_due_not_overdue(
        self, db_session, test_org, test_user, test_agreement
    ):
        obligation = await _mk_obligation(
            db_session, test_agreement, due_offset_days=0
        )
        await db_session.commit()

        result = await notify_insurance_expiry(db_session)

        assert result["expired"] == 0
        assert result["expiring_soon"] == 1
        reminders = await _reminders(db_session, obligation)
        assert reminders[0].reminder_type == "due"

    async def test_idempotent_across_sweeps(
        self, db_session, test_org, test_user, test_agreement
    ):
        obligation = await _mk_obligation(
            db_session, test_agreement, due_offset_days=5
        )
        await db_session.commit()

        first = await notify_insurance_expiry(db_session)
        second = await notify_insurance_expiry(db_session)

        assert first["notified"] == 1
        assert second["notified"] == 0
        assert second["skipped"] == 1
        assert len(await _reminders(db_session, obligation)) == 1

    async def test_failed_delivery_is_retried(
        self, db_session, test_org, test_user, test_agreement
    ):
        pref = NotificationPreference(
            user_id=test_user.id,
            organization_id=test_org.id,
            email_obligation_reminder=False,
        )
        db_session.add(pref)
        obligation = await _mk_obligation(
            db_session, test_agreement, due_offset_days=5
        )
        await db_session.commit()

        first = await notify_insurance_expiry(db_session)
        assert first["notified"] == 0
        reminders = await _reminders(db_session, obligation)
        assert reminders[0].status == "failed"

        pref.email_obligation_reminder = True
        await db_session.flush()
        second = await notify_insurance_expiry(db_session)

        assert second["notified"] == 1
        assert second["skipped"] == 0
        reminders = await _reminders(db_session, obligation)
        assert len(reminders) == 2
        assert {r.status for r in reminders} == {"failed", "sent"}

    async def test_ignores_non_insurance_obligations(
        self, db_session, test_org, test_user, test_agreement
    ):
        await _mk_obligation(
            db_session, test_agreement, due_offset_days=5,
            obligation_type="reporting",
        )
        await db_session.commit()

        result = await notify_insurance_expiry(db_session)

        assert result["obligations_checked"] == 0
        assert result["notified"] == 0

    async def test_matches_bond_obligations(
        self, db_session, test_org, test_user, test_agreement
    ):
        await _mk_obligation(
            db_session, test_agreement, due_offset_days=5, obligation_type="bond"
        )
        await db_session.commit()

        result = await notify_insurance_expiry(db_session)

        assert result["obligations_checked"] == 1
        assert result["notified"] == 1

    async def test_ignores_cover_outside_lookahead(
        self, db_session, test_org, test_user, test_agreement
    ):
        await _mk_obligation(db_session, test_agreement, due_offset_days=60)
        await db_session.commit()

        result = await notify_insurance_expiry(db_session)

        assert result["obligations_checked"] == 0
        assert result["notified"] == 0

    async def test_honors_custom_lookahead(
        self, db_session, test_org, test_user, test_agreement
    ):
        await _mk_obligation(db_session, test_agreement, due_offset_days=20)
        await db_session.commit()

        inside = await notify_insurance_expiry(db_session, lookahead_days=30)
        assert inside["obligations_checked"] == 1

        outside = await notify_insurance_expiry(db_session, lookahead_days=10)
        assert outside["obligations_checked"] == 0

    async def test_ignores_completed_obligations(
        self, db_session, test_org, test_user, test_agreement
    ):
        await _mk_obligation(
            db_session, test_agreement, due_offset_days=5, status="completed"
        )
        await db_session.commit()

        result = await notify_insurance_expiry(db_session)

        assert result["obligations_checked"] == 0
