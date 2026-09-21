"""Spec §67 — READY_FOR_SIGNATURE → EXECUTED only after all required signers."""

import hashlib
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import AgreementVersion
from app.models.agreement_access import AgreementParty
from app.models.external_party import ExternalPartySignature
from app.models.signature import InternalSignature
from app.services.external_party_service import create_external_party
from app.services.signing_completion import check_and_execute, signature_progress


async def _version(db: AsyncSession, agreement, user) -> AgreementVersion:
    v = AgreementVersion(
        agreement_id=agreement.id,
        version_number=1,
        content="Agreement text",
        content_hash=hashlib.sha256(b"Agreement text").hexdigest(),
        status="draft",
        created_by=user.id,
    )
    db.add(v)
    await db.flush()
    return v


async def _external_party(db: AsyncSession, agreement, legal_entity, email: str):
    party = AgreementParty(
        agreement_id=agreement.id,
        legal_entity_id=legal_entity.id,
        party_role="receiving",
        signatory_required=True,
    )
    db.add(party)
    await db.flush()
    return await create_external_party(
        db,
        agreement_id=agreement.id,
        agreement_party_id=party.id,
        company_name="Counterparty Ltd",
        signatory_name="Jane Signer",
        signatory_email=email,
    )


def _internal_sig(agreement, user, version):
    return InternalSignature(
        agreement_id=agreement.id,
        user_id=user.id,
        version_id=version.id,
        signed_at=datetime.now(timezone.utc),
        consent_text="I agree",
        signature_hash=uuid.uuid4().hex,
    )


def _external_sig(agreement, party, version):
    return ExternalPartySignature(
        external_party_id=party.id,
        agreement_id=agreement.id,
        version_id=version.id,
        signed_at=datetime.now(timezone.utc),
        consent_text="I agree",
        signature_hash=uuid.uuid4().hex,
    )


@pytest.mark.asyncio
async def test_progress_lists_outstanding_signers(db_session, test_agreement, test_user, test_legal_entity):
    version = await _version(db_session, test_agreement, test_user)
    party = await _external_party(db_session, test_agreement, test_legal_entity, "jane@counterparty.example")
    db_session.add(_internal_sig(test_agreement, test_user, version))
    await db_session.flush()

    progress = await signature_progress(db_session, test_agreement.id)
    assert progress.internal_signatures == 1
    assert progress.required_external == 1
    assert progress.signed_external == 0
    assert progress.missing_external == [party.signatory_email]
    assert progress.all_signed is False


@pytest.mark.asyncio
async def test_internal_signature_alone_does_not_execute(db_session, test_agreement, test_user, test_legal_entity):
    """Regression: previously any single signature auto-executed the agreement."""
    version = await _version(db_session, test_agreement, test_user)
    await _external_party(db_session, test_agreement, test_legal_entity, "jane@counterparty.example")
    db_session.add(_internal_sig(test_agreement, test_user, version))
    test_agreement.status = "signing"
    await db_session.flush()

    executed = await check_and_execute(db_session, agreement=test_agreement, org_id=test_agreement.organization_id)
    assert executed is False
    assert test_agreement.status == "partially_signed"
    assert test_agreement.execution_date is None


@pytest.mark.asyncio
async def test_all_required_signers_executes_and_locks(db_session, test_agreement, test_user, test_legal_entity):
    version = await _version(db_session, test_agreement, test_user)
    party = await _external_party(db_session, test_agreement, test_legal_entity, "jane@counterparty.example")
    db_session.add(_internal_sig(test_agreement, test_user, version))
    db_session.add(_external_sig(test_agreement, party, version))
    test_agreement.status = "partially_signed"
    await db_session.flush()

    executed = await check_and_execute(db_session, agreement=test_agreement, org_id=test_agreement.organization_id)
    assert executed is True
    assert test_agreement.status == "executed"
    assert test_agreement.execution_date is not None
    await db_session.refresh(version)
    assert version.status == "locked"


@pytest.mark.asyncio
async def test_declined_party_is_not_a_required_signer(db_session, test_agreement, test_user, test_legal_entity):
    version = await _version(db_session, test_agreement, test_user)
    party = await _external_party(db_session, test_agreement, test_legal_entity, "jane@counterparty.example")
    party.status = "rejected"
    db_session.add(_internal_sig(test_agreement, test_user, version))
    await db_session.flush()

    progress = await signature_progress(db_session, test_agreement.id)
    assert progress.required_external == 0
    assert progress.all_signed is True


@pytest.mark.asyncio
async def test_generic_execute_transition_refused_without_signatures(
    db_session, test_agreement, test_user, test_legal_entity
):
    """The `all_signed` rule condition must be enforced by apply_transition
    itself, so /workflow/transition and webhooks cannot force EXECUTED."""
    from app.services.lifecycle_service import TransitionNotAllowed, apply_transition

    await _external_party(db_session, test_agreement, test_legal_entity, "jane@counterparty.example")
    test_agreement.status = "signing"
    await db_session.flush()

    with pytest.raises(TransitionNotAllowed, match="still waiting on"):
        await apply_transition(
            db_session,
            agreement=test_agreement,
            action_key="execute",
            actor_id=test_user.id,
            org_id=test_agreement.organization_id,
        )
    assert test_agreement.status == "signing"


@pytest.mark.asyncio
async def test_executed_agreement_is_never_reprocessed(db_session, test_agreement, test_user):
    version = await _version(db_session, test_agreement, test_user)
    db_session.add(_internal_sig(test_agreement, test_user, version))
    test_agreement.status = "executed"
    await db_session.flush()
    assert await check_and_execute(db_session, agreement=test_agreement, org_id=test_agreement.organization_id) is False


@pytest.mark.asyncio
async def test_seal_without_explicit_provider_resolves_config_provider(
    db_session, test_agreement, test_user
):
    """Regression (two bugs):

    1. seal() read a nonexistent ``esign_provider`` setting, so it always
       silently used the mock provider even when DocuSign/Adobe Sign was
       configured. It must now resolve via resolve_esignature_provider().
    2. seal() read nonexistent ``agreement.envelope_id`` attributes, so it
       always returned early. The envelope id lives on the agreement's
       SignatureRequest.metadata_json.provider_envelope_id.
    """
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock, patch

    from app.models.execution import SignatureRequest
    from app.models.agreement import AgreementVersion
    from app.services.esignature import MockESignatureProvider

    version = AgreementVersion(
        agreement_id=test_agreement.id,
        version_number=1,
        content="Agreement text",
        content_hash=hashlib.sha256(b"Agreement text").hexdigest(),
        status="draft",
        created_by=test_user.id,
    )
    db_session.add(version)
    await db_session.flush()

    db_session.add(
        SignatureRequest(
            tenant_id=test_agreement.organization_id,
            agreement_id=test_agreement.id,
            version_id=version.id,
            name="Jane Signer",
            email="jane@counterparty.example",
            created_by=test_user.id,
            metadata_json={"provider_envelope_id": "env-regression-1"},
        )
    )
    await db_session.flush()

    fake_provider = MockESignatureProvider()
    fake_provider.download_signed_document = AsyncMock(return_value=b"%PDF-1.4 signed")

    with patch(
        "app.services.esignature.resolve_esignature_provider",
        return_value=fake_provider,
    ) as resolver:
        from app.services.signing_completion import seal

        await seal(db_session, test_agreement, provider=None, org_id=test_agreement.organization_id)

    resolver.assert_called_once()
    fake_provider.download_signed_document.assert_awaited_once_with("env-regression-1")
    assert test_agreement.sealed_document_key == (
        f"agreements/{test_agreement.organization_id}/{test_agreement.id}/"
        f"signed_env-regression-1.pdf"
    )
    assert test_agreement.sealed_at is not None
    assert isinstance(test_agreement.sealed_at, datetime)
