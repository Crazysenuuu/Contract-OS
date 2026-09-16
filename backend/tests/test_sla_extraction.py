"""Tests for SLA field extraction and monitoring (spec §11).

Covers:
- deterministic extraction captures uptime / response-time metrics
- SLA candidates carry their metrics into obligation metadata
- the SLA sweep materialises review deadlines per measurement period
- lapsed SLA reviews are flagged as breaches (OVERDUE + SLA_BREACH event)
"""

import re
from datetime import datetime, timedelta, timezone

import pytest_asyncio

from app.models.agreement import Agreement
from app.models.obligation import Obligation
from app.services.obligation_extractor import extract_candidates_deterministic
from app.services.sla_service import (
    DEADLINE_TYPE,
    detect_sla_violations,
    next_period_end,
)
from sqlalchemy import select

from app.models.obligation import ObligationDeadline

SLA_TEXT = """
MASTER SERVICES AGREEMENT - SCHEDULE C (SERVICE LEVELS)

1. Availability
The Service Provider shall maintain an uptime of 99.9% measured monthly.

2. Incident Response
The Service Provider shall respond to P1 incidents within 30 minutes.
"""


def _normalised(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _sla_candidate(cands):
    return next(c for c in cands if c["obligation_type"] == "sla")


def _candidate_with(cands, metric_key):
    return next(
        c
        for c in cands
        if (c.get("sla_metrics") or {}).get(metric_key) is not None
    )


# ===== Extraction =====


def test_sla_uptime_extracted():
    cands = extract_candidates_deterministic(SLA_TEXT)
    sla = _sla_candidate(cands)
    assert sla["source_text"] in _normalised(SLA_TEXT)
    metrics = sla["sla_metrics"]
    assert metrics["uptime_target"] == 99.9
    assert metrics["measurement_period"] == "monthly"


def test_sla_response_time_extracted():
    cands = extract_candidates_deterministic(SLA_TEXT)
    sla = _candidate_with(cands, "response_time_hours")
    metrics = sla["sla_metrics"]
    assert metrics["response_time_hours"] == 0.5  # 30 minutes


def test_plain_maintenance_is_not_sla():
    """Uptime keywords without a measurable percentage stay non-SLA."""
    text = "The Supplier shall maintain an uptime appropriate to the Services."
    cands = extract_candidates_deterministic(text)
    assert all(c["obligation_type"] != "sla" for c in cands)


# ===== Sweep service =====


@pytest_asyncio.fixture
async def sla_agreement(db_session, test_user, test_org, test_agreement_type):
    agreement = Agreement(
        organization_id=test_org.id,
        agreement_type_id=test_agreement_type.id,
        title="MSA with SLA schedule",
        status="active",
        created_by=test_user.id,
        data={},
    )
    db_session.add(agreement)
    await db_session.commit()
    await db_session.refresh(agreement)
    return agreement


@pytest_asyncio.fixture
async def sla_obligation(db_session, sla_agreement, test_user, test_org):
    obligation = Obligation(
        agreement_id=sla_agreement.id,
        organization_id=test_org.id,
        obligation_type="sla",
        description="Maintain 99.9% uptime measured monthly",
        owner_party="supplier",
        status="CONFIRMED",
        metadata_json={
            "sla_metrics": {
                "uptime_target": 99.9,
                "measurement_period": "monthly",
            }
        },
    )
    db_session.add(obligation)
    await db_session.commit()
    await db_session.refresh(obligation)
    return obligation


async def _sla_deadlines(db, obligation_id):
    result = await db.execute(
        select(ObligationDeadline)
        .where(ObligationDeadline.obligation_id == obligation_id)
        .order_by(ObligationDeadline.due_at)
    )
    return [
        d for d in result.scalars().all()
        if d.deadline_type == DEADLINE_TYPE
    ]


async def test_sweep_creates_period_review_deadline(db_session, sla_obligation):
    result = await detect_sla_violations(db_session)
    assert result["deadlines_created"] == 1
    assert result["breach_count"] == 0

    deadlines = await _sla_deadlines(db_session, sla_obligation.id)
    assert len(deadlines) == 1
    assert deadlines[0].status == "OPEN"
    assert deadlines[0].calculation_rule["measurement_period"] == "monthly"
    due_at = deadlines[0].due_at
    if due_at.tzinfo is None:  # SQLite round-trips datetimes naive
        due_at = due_at.replace(tzinfo=timezone.utc)
    assert due_at > datetime.now(timezone.utc)

    # Idempotent: the second sweep must not duplicate the deadline.
    again = await detect_sla_violations(db_session)
    assert again["deadlines_created"] == 0


async def test_lapsed_sla_is_flagged_as_breach(db_session, sla_obligation):
    # First sweep schedules the monthly review, then push it into the past.
    await detect_sla_violations(db_session)
    deadlines = await _sla_deadlines(db_session, sla_obligation.id)
    deadlines[0].due_at = datetime.now(timezone.utc) - timedelta(days=1)
    await db_session.commit()

    result = await detect_sla_violations(db_session)
    assert result["breach_count"] == 1
    breach = result["breaches"][0]
    assert str(sla_obligation.id) == breach["obligation_id"]
    assert breach["sla_metrics"]["uptime_target"] == 99.9

    await db_session.refresh(sla_obligation)
    assert sla_obligation.status == "OVERDUE"


def test_next_period_end_periods():
    now = datetime(2026, 9, 16, 15, 30)
    assert next_period_end(now, "monthly").day == 1
    assert next_period_end(now, "monthly").month == 10
    assert next_period_end(now, "weekly").weekday() == 0
    assert next_period_end(now, "quarterly").month == 10
    assert next_period_end(now, "annually").year == 2027
