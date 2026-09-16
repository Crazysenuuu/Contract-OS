"""Tests for guest ID verification (spec 24.3).

When ``requires_id_verification`` is set on an external party, the guest
must complete an email OTP challenge before accepting or signing; viewing
and commenting stay frictionless.
"""

import uuid

import pytest

from app.models.external_party import ExternalParty
from app.services.external_party_service import (
    complete_id_verification,
    ensure_id_verified,
    start_id_verification,
)


async def _make_party(
    db,
    agreement,
    *,
    requires_idv: bool = True,
    legal_entity_id: uuid.UUID | None = None,
) -> ExternalParty:
    from app.models.agreement_access import AgreementParty

    party_row = AgreementParty(
        agreement_id=agreement.id,
        legal_entity_id=legal_entity_id or uuid.uuid4(),
        party_role="receiving",
    )
    db.add(party_row)
    await db.flush()

    party = ExternalParty(
        agreement_id=agreement.id,
        agreement_party_id=party_row.id,
        company_name="Counterparty Inc",
        signatory_name="Jane Guest",
        signatory_email="jane@counterparty.com",
        access_token="tok-" + uuid.uuid4().hex,
        expires_at=None,
        status="pending",
        requires_id_verification=requires_idv,
    )
    db.add(party)
    await db.flush()
    return party


def test_ensure_id_verified_passes_when_not_required():
    party = ExternalParty(requires_id_verification=False, id_verified_at=None)
    ensure_id_verified(party)  # must not raise


def test_ensure_id_verified_passes_after_verification():
    from datetime import datetime, timezone

    party = ExternalParty(
        requires_id_verification=True,
        id_verified_at=datetime.now(timezone.utc),
    )
    ensure_id_verified(party)  # must not raise


def test_ensure_id_verified_blocks_unverified():
    party = ExternalParty(requires_id_verification=True, id_verified_at=None)
    with pytest.raises(PermissionError):
        ensure_id_verified(party)


@pytest.mark.asyncio
async def test_start_id_verification_issues_challenge(
    db_session, test_agreement
):
    party = await _make_party(db_session, test_agreement)

    challenge = await start_id_verification(db_session, party)
    assert challenge["challenge_id"]
    assert challenge["expires_at"]


@pytest.mark.asyncio
async def test_complete_verification_marks_party_verified(
    db_session, test_agreement
):
    from datetime import datetime, timezone

    party = await _make_party(db_session, test_agreement)
    challenge = await start_id_verification(db_session, party)

    # The raw code comes back on the challenge payload (dev/log path).
    code = challenge["debug_code"]
    assert code

    await complete_id_verification(
        db_session,
        party,
        challenge_id=uuid.UUID(challenge["challenge_id"]),
        code=code,
    )
    assert party.id_verified_at is not None
    assert party.id_verification_method == "otp_email"
    ensure_id_verified(party)  # gate now passes


@pytest.mark.asyncio
async def test_complete_verification_rejects_wrong_code(
    db_session, test_agreement
):
    party = await _make_party(db_session, test_agreement)
    challenge = await start_id_verification(db_session, party)

    with pytest.raises(Exception):
        await complete_id_verification(
            db_session,
            party,
            challenge_id=uuid.UUID(challenge["challenge_id"]),
            code="000000" if challenge["debug_code"] != "000000" else "111111",
        )
    assert party.id_verified_at is None
