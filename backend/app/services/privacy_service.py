"""Data privacy service (spec 24.3): cryptographic shredding + redaction.

- encrypt_field: encrypts a PII value with a per-record Fernet key. The key
  itself is wrapped (encrypted) under a master key and stored as a key
  envelope; only the envelope reference is persisted with the record.
- shred_field: permanently deletes the key envelope. The ciphertext remains
  in the ledger (structural integrity preserved) but is permanently
  unreadable — this is cryptographic shredding.
- Redaction: redact_field_paths rewrites an agreement's rendered document
  text/HTML replacing targeted PII with [REDACTED] while leaving the rest
  intact.
- Erasure: orchestrates the right-to-be-forgotten workflow, preserving an
  evidentiary note about what was kept and why.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.privacy import (
    ErasureRequest,
    FieldEncryptionRecord,
    RedactionRequest,
)

# Sensitive agreement.data keys considered PII by default.
PII_FIELD_HINTS = (
    "email", "phone", "phone_number", "passport", "national_id",
    "id_number", "dob", "birth", "ssn", "nin", "address_line",
)


class PrivacyError(Exception):
    """Raised on privacy-operation failures."""


def _master_key() -> bytes:
    key = os.environ.get("FIELD_ENCRYPTION_MASTER_KEY")
    if key:
        return key.encode("utf-8")
    # Deterministic dev fallback — NOT for production use.
    from app.core.config import get_settings_lazy

    settings = get_settings_lazy()
    return _derive_dev_key(settings.jwt_secret_key.get_secret_value())


def _derive_dev_key(secret: str) -> bytes:
    import base64
    import hashlib

    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fernet() -> Fernet:
    return Fernet(_master_key())


def _new_data_key() -> bytes:
    return Fernet.generate_key()


def encrypt_value(value: str) -> tuple[str, str]:
    """Encrypt a value with a fresh per-record key.

    Returns (envelope_id, ciphertext) where the key envelope (Fernet token
    wrapping the per-record key under the master key) is what gets deleted
    to shred the value.
    """
    data_key = _new_data_key()
    envelope = _fernet().encrypt(data_key)
    ciphertext = Fernet(data_key).encrypt(value.encode("utf-8")).decode("utf-8")
    return envelope.decode("utf-8"), ciphertext


def decrypt_value(envelope_id: str, ciphertext: str) -> str:
    """Decrypt a value given its key envelope id."""
    try:
        data_key = _fernet().decrypt(envelope_id.encode("utf-8"))
        return Fernet(data_key).decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as e:
        raise PrivacyError("Field is shredded or key envelope is invalid") from e


async def encrypt_agreement_pii(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    agreement_id: uuid.UUID,
    data: dict,
    encrypt_all_fields: bool = False,
) -> dict:
    """Encrypt PII-looking fields of agreement.data in place.

    Returns the (mutated) data dict where sensitive values are replaced by
    a marker, with the ciphertext + envelope persisted in
    field_encryption_records. Non-sensitive fields remain plain text so the
    commercial terms stay readable.
    """
    result = {}
    for key, value in data.items():
        lower = key.lower()
        is_pii = encrypt_all_fields or any(h in lower for h in PII_FIELD_HINTS)
        if is_pii and isinstance(value, str) and value.strip():
            envelope_id, ciphertext = encrypt_value(value)
            record = FieldEncryptionRecord(
                organization_id=organization_id,
                agreement_id=agreement_id,
                field_path=key,
                data_class="pii",
                key_envelope_id=envelope_id,
                ciphertext=ciphertext,
            )
            db.add(record)
            result[key] = "[ENCRYPTED]"
        else:
            result[key] = value
    await db.flush()
    return result


async def list_encrypted_fields(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    agreement_id: uuid.UUID,
) -> list[dict]:
    result = await db.execute(
        select(FieldEncryptionRecord).where(
            FieldEncryptionRecord.organization_id == organization_id,
            FieldEncryptionRecord.agreement_id == agreement_id,
        )
    )
    return [
        {
            "id": str(r.id),
            "field_path": r.field_path,
            "data_class": r.data_class,
            "shredded": r.shredded,
            "shredded_at": r.shredded_at.isoformat() if r.shredded_at else None,
        }
        for r in result.scalars().all()
    ]


async def shred_field(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    agreement_id: uuid.UUID,
    field_path: str,
) -> FieldEncryptionRecord:
    """Cryptographically shred one field: delete its key envelope.

    The ciphertext stays in the ledger; it simply becomes unreadable.
    """
    result = await db.execute(
        select(FieldEncryptionRecord).where(
            FieldEncryptionRecord.organization_id == organization_id,
            FieldEncryptionRecord.agreement_id == agreement_id,
            FieldEncryptionRecord.field_path == field_path,
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise PrivacyError("No encrypted record for this field")

    if record.shredded:
        raise PrivacyError("Field already shredded")

    record.key_envelope_id = ""  # Destroy the key envelope.
    record.shredded = True
    record.shredded_at = _now()
    await db.flush()
    return record


async def create_redaction_request(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    agreement_id: uuid.UUID,
    targets: dict,
    reason: str,
    requested_by: uuid.UUID,
    document_type: str = "executed",
) -> RedactionRequest:
    req = RedactionRequest(
        organization_id=organization_id,
        agreement_id=agreement_id,
        document_type=document_type,
        targets=targets,
        reason=reason,
        status="pending",
        requested_by=requested_by,
    )
    db.add(req)
    await db.flush()
    return req


def redact_text(text: str, targets: dict) -> str:
    """Redact targeted PII from a rendered document text.

    Replaces field-path values and literal text patterns with [REDACTED]
    while leaving everything else intact.
    """
    redacted = text
    for pattern in targets.get("text_patterns", []):
        if not pattern:
            continue
        redacted = redacted.replace(str(pattern), "[REDACTED]")

    for field in targets.get("field_paths", []):
        # Match "key: value" style occurrences.
        import re

        redacted = re.sub(
            rf"(?i)({re.escape(str(field))}\s*[:=]\s*)([^\n,;]+)",
            r"\1[REDACTED]",
            redacted,
        )
    return redacted


async def complete_redaction(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    request_id: uuid.UUID,
    redacted_content: str,
    processed_by: uuid.UUID,
) -> RedactionRequest:
    """Mark a redaction request completed and store the redacted document."""
    result = await db.execute(
        select(RedactionRequest).where(
            RedactionRequest.id == request_id,
            RedactionRequest.organization_id == organization_id,
        )
    )
    req = result.scalar_one_or_none()
    if req is None:
        raise PrivacyError("Redaction request not found")

    content_ref = f"{organization_id}/{req.agreement_id}/redacted-{uuid.uuid4().hex}.txt"
    from app.services import document_storage

    document_storage.store_blob(content_ref, redacted_content.encode("utf-8"))
    req.status = "completed"
    req.redacted_content_ref = content_ref
    req.processed_by = processed_by
    req.processed_at = _now()
    await db.flush()
    return req


async def create_erasure_request(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    agreement_id: uuid.UUID | None,
    data_subject: str,
    regulation: str,
    requested_by: uuid.UUID,
) -> ErasureRequest:
    req = ErasureRequest(
        organization_id=organization_id,
        agreement_id=agreement_id,
        data_subject=data_subject,
        regulation=regulation,
        status="received",
        requested_by=requested_by,
    )
    db.add(req)
    await db.flush()
    return req


async def execute_erasure(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    request_id: uuid.UUID,
    field_paths: list[str] | None = None,
    agreement_id: uuid.UUID | None = None,
    preserved_notes: str | None = None,
) -> ErasureRequest:
    """Execute an erasure: shred targeted fields (or all on the agreement).

    Commercial terms and the ledger structure are preserved; the PII key
    envelopes are destroyed (cryptographic shredding).
    """
    result = await db.execute(
        select(ErasureRequest).where(
            ErasureRequest.id == request_id,
            ErasureRequest.organization_id == organization_id,
        )
    )
    req = result.scalar_one_or_none()
    if req is None:
        raise PrivacyError("Erasure request not found")

    target_agreement = agreement_id or req.agreement_id
    if target_agreement is None:
        raise PrivacyError("No agreement targeted for erasure")

    query = select(FieldEncryptionRecord).where(
        FieldEncryptionRecord.organization_id == organization_id,
        FieldEncryptionRecord.agreement_id == target_agreement,
    )
    if field_paths:
        query = query.where(FieldEncryptionRecord.field_path.in_(field_paths))

    records = (await db.execute(query)).scalars().all()
    shredded = []
    for rec in records:
        if not rec.shredded:
            rec.key_envelope_id = ""
            rec.shredded = True
            rec.shredded_at = _now()
            shredded.append(rec.field_path)

    req.status = "shredded"
    req.shredded_fields = shredded
    req.preserved_notes = preserved_notes
    req.completed_at = _now()
    await db.flush()
    return req