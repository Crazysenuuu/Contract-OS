"""Tests for data privacy: field-level encryption, crypto shredding,
redaction, and right-to-be-forgotten (spec 24.3)."""

import pytest
from sqlalchemy import select

from app.models.privacy import (
    ErasureRequest,
    FieldEncryptionRecord,
    RedactionRequest,
)
from app.services.privacy_service import (
    PrivacyError,
    complete_redaction,
    create_erasure_request,
    create_redaction_request,
    decrypt_value,
    encrypt_agreement_pii,
    encrypt_value,
    execute_erasure,
    list_encrypted_fields,
    redact_text,
    shred_field,
)


@pytest.mark.asyncio
async def test_encrypt_and_decrypt_roundtrip():
    envelope_id, ciphertext = encrypt_value("jane@acme.com")
    assert ciphertext != "jane@acme.com"
    assert decrypt_value(envelope_id, ciphertext) == "jane@acme.com"


@pytest.mark.asyncio
async def test_encrypt_agreement_pii_leaves_commercial_terms_plain(
    db_session, test_org, test_agreement
):
    data = {
        "party_a_contact_email": "jane@acme.com",
        "party_b_contact_email": "john@globex.com",
        "liability_cap": "USD 1,000,000",
        "term_months": "12",
    }
    encrypted = await encrypt_agreement_pii(
        db_session,
        organization_id=test_org.id,
        agreement_id=test_agreement.id,
        data=data,
    )
    assert encrypted["party_a_contact_email"] == "[ENCRYPTED]"
    assert encrypted["party_b_contact_email"] == "[ENCRYPTED]"
    # Commercial terms remain readable.
    assert encrypted["liability_cap"] == "USD 1,000,000"
    assert encrypted["term_months"] == "12"

    fields = await list_encrypted_fields(
        db_session,
        organization_id=test_org.id,
        agreement_id=test_agreement.id,
    )
    assert len(fields) == 2
    assert {f["field_path"] for f in fields} == {
        "party_a_contact_email",
        "party_b_contact_email",
    }
    # Ciphertext is recoverable before shredding.
    records = (
        await db_session.execute(
            select(FieldEncryptionRecord).where(
                FieldEncryptionRecord.agreement_id == test_agreement.id
            )
        )
    ).scalars().all()
    for rec in records:
        assert decrypt_value(rec.key_envelope_id, rec.ciphertext).startswith(
            ("jane@", "john@")
        )


@pytest.mark.asyncio
async def test_crypto_shredding_makes_value_unreadable(db_session, test_org, test_agreement):
    await encrypt_agreement_pii(
        db_session,
        organization_id=test_org.id,
        agreement_id=test_agreement.id,
        data={"party_a_contact_email": "secret@example.com"},
    )
    record = (
        await db_session.execute(
            select(FieldEncryptionRecord).where(
                FieldEncryptionRecord.agreement_id == test_agreement.id
            )
        )
    ).scalar_one()
    # Pre-shred: readable.
    assert decrypt_value(record.key_envelope_id, record.ciphertext) == "secret@example.com"

    await shred_field(
        db_session,
        organization_id=test_org.id,
        agreement_id=test_agreement.id,
        field_path=record.field_path,
    )
    fields = await list_encrypted_fields(
        db_session,
        organization_id=test_org.id,
        agreement_id=test_agreement.id,
    )
    shredded = fields[0]
    assert shredded["shredded"] is True
    # Double shred is rejected.
    with pytest.raises(PrivacyError):
        await shred_field(
            db_session,
            organization_id=test_org.id,
            agreement_id=test_agreement.id,
            field_path=record.field_path,
        )


@pytest.mark.asyncio
async def test_redact_text_masks_patterns_and_fields():
    targets = {
        "field_paths": ["party_email"],
        "text_patterns": ["john@example.com"],
    }
    text = "Contact: john@example.com. Deal value remains visible at $50,000."
    redacted = redact_text(text, targets)
    assert "john@example.com" not in redacted
    assert "50,000" in redacted
    assert "[REDACTED]" in redacted


@pytest.mark.asyncio
async def test_redaction_request_lifecycle(db_session, test_org, test_agreement, test_user):
    req = await create_redaction_request(
        db_session,
        organization_id=test_org.id,
        agreement_id=test_agreement.id,
        targets={"field_paths": ["party_a_contact_email"]},
        reason="Data subject request",
        requested_by=test_user.id,
    )
    assert req.status == "pending"

    done = await complete_redaction(
        db_session,
        organization_id=test_org.id,
        request_id=req.id,
        redacted_content="[REDACTED] agreement body",
        processed_by=test_user.id,
    )
    assert done.status == "completed"
    assert done.redacted_content_ref and done.redacted_content_ref.endswith(".txt")


@pytest.mark.asyncio
async def test_erasure_request_shreds_pii_and_records_preservation(
    db_session, test_org, test_agreement, test_user
):
    data = await encrypt_agreement_pii(
        db_session,
        organization_id=test_org.id,
        agreement_id=test_agreement.id,
        data={"signer_email": "alice@example.com", "title": "Master Agreement"},
    )
    assert data["signer_email"] == "[ENCRYPTED]"

    req = await create_erasure_request(
        db_session,
        organization_id=test_org.id,
        agreement_id=test_agreement.id,
        data_subject="alice@example.com",
        regulation="gdpr",
        requested_by=test_user.id,
    )
    assert req.status == "received"

    result = await execute_erasure(
        db_session,
        organization_id=test_org.id,
        request_id=req.id,
        preserved_notes="Commercial terms retained for audit (Art. 17(3)(e))",
    )
    assert result.status == "shredded"
    assert result.shredded_fields
    assert result.preserved_notes

    fields = await list_encrypted_fields(
        db_session,
        organization_id=test_org.id,
        agreement_id=test_agreement.id,
    )
    # The signer email field is now shredded.
    assert all(f["shredded"] for f in fields if f["field_path"] == "signer_email")