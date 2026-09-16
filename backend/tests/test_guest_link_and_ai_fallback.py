"""Guest-portal link expiry + honest AI fallback (spec 24 / gap E2-H2).

Verifies that:
- New guest review links carry a default expiry (time-bound access).
- validate_access_token refuses an expired link.
- The AI service never fabricates risk findings when no LLM is configured;
  it returns an empty result marked analysis_status=not_analyzed.
"""

import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def _add_party(db_session, agreement_id, org_id):
    from app.models.agreement_access import AgreementParty
    from app.models.legal_entity import LegalEntity

    entity = LegalEntity(
        organization_id=org_id,
        legal_name="Counterparty Ltd",
        country="LK",
        entity_type="corporation",
    )
    db_session.add(entity)
    await db_session.flush()
    party = AgreementParty(
        agreement_id=agreement_id,
        legal_entity_id=entity.id,
        party_role="receiving",
        display_name="Counterparty Ltd",
    )
    db_session.add(party)
    await db_session.flush()
    return party


async def test_guest_link_gets_default_expiry(
    db_session, test_agreement, test_org
):
    from app.services.external_party_service import (
        GUEST_LINK_TTL_DAYS,
        create_external_party,
    )

    party = await _add_party(db_session, test_agreement.id, test_org.id)
    external = await create_external_party(
        db_session,
        agreement_id=test_agreement.id,
        agreement_party_id=party.id,
        company_name="Counterparty Ltd",
        signatory_name="Jane Doe",
        signatory_email="jane@counterparty.com",
    )
    assert external.expires_at is not None
    assert external.expires_at > datetime.now(timezone.utc)
    assert external.expires_at <= (
        datetime.now(timezone.utc) + timedelta(days=GUEST_LINK_TTL_DAYS + 1)
    )


async def test_expired_guest_link_is_refused(
    db_session, test_agreement, test_org
):
    from app.services.external_party_service import (
        create_external_party,
        validate_access_token,
    )

    party = await _add_party(db_session, test_agreement.id, test_org.id)
    external = await create_external_party(
        db_session,
        agreement_id=test_agreement.id,
        agreement_party_id=party.id,
        company_name="Counterparty Ltd",
        signatory_name="Jane Doe",
        signatory_email="jane@counterparty.com",
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    assert await validate_access_token(db_session, external.access_token) is None


async def test_guest_link_marked_expired_not_used(db_session, test_agreement, test_org):
    from app.services.external_party_service import (
        create_external_party,
        validate_access_token,
    )

    party = await _add_party(db_session, test_agreement.id, test_org.id)
    expired = await create_external_party(
        db_session,
        agreement_id=test_agreement.id,
        agreement_party_id=party.id,
        company_name="Counterparty Ltd",
        signatory_name="Jane Doe",
        signatory_email="jane@counterparty.com",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    active = await create_external_party(
        db_session,
        agreement_id=test_agreement.id,
        agreement_party_id=party.id,
        company_name="Counterparty Ltd",
        signatory_name="Jane Doe",
        signatory_email="jane@counterparty.com",
    )
    assert await validate_access_token(db_session, expired.access_token) is None
    assert await validate_access_token(db_session, active.access_token) is not None


def test_ai_fallback_never_fabricates_risks():
    from app.services.ai_service import AIService

    ai = AIService()
    response = ai._generate_fallback_response(
        "Analyze the risk of this contract: <contract/>"
    )
    assert "Confidentiality scope should be reviewed" not in response

    payload = ai._parse_risks_response(response)
    assert isinstance(payload, list)
    assert payload == []

    analysis = ai._parse_analysis_response(response)
    assert analysis.risks == []
    assert analysis.summary == ""


def test_ai_fallback_marked_not_analyzed():
    from app.services.ai_service import AIService

    ai = AIService()
    response = ai._generate_fallback_response("Analyze contract")
    import json

    data = json.loads(response)
    assert data["analysis_status"] == "not_analyzed"
    assert data["risks"] == []