"""Signing completion engine (spec §21, §67).

``READY_FOR_SIGNATURE → EXECUTED`` is only legal *after all required
signers have completed*. This module is the single place that decides
whether that condition holds and drives the agreement through
``signing → partially_signed → executed``.

Required signers are:

* every :class:`ExternalParty` on the agreement that is allowed to sign
  (``can_sign``) and has not declined/rejected; and
* at least one internal signatory (an :class:`InternalSignature` row).

Both the internal sign endpoint and the external tokenised sign endpoint
call :func:`check_and_execute` after recording a signature, so the two
paths can never disagree about when an agreement becomes executed.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agreement_states import IMMUTABLE_STATES, AgreementStatus
from app.models.agreement import Agreement
from app.models.external_party import ExternalParty, ExternalPartySignature
from app.models.signature import InternalSignature
from app.services.agreement_versioning import get_latest_version, lock_version
from app.services.audit_service import record_event
from app.services.lifecycle_service import TransitionNotAllowed, apply_transition

if TYPE_CHECKING:
    from app.services.esignature import ESignatureProvider

# Sentinel UUID used when a transition is triggered by the system (no human actor).
_SYSTEM_ACTOR_ID = uuid.UUID(int=0)

# External party statuses that remove the party from the required-signer set.
_NON_SIGNING_EXTERNAL_STATUSES = frozenset({"rejected", "declined", "revoked"})


@dataclass
class SignatureProgress:
    required_external: int
    signed_external: int
    internal_signatures: int
    missing_external: list[str] = field(default_factory=list)

    @property
    def all_signed(self) -> bool:
        return (
            self.signed_external >= self.required_external
            and self.internal_signatures >= 1
        )

    @property
    def total_signatures(self) -> int:
        return self.signed_external + self.internal_signatures

    def to_dict(self) -> dict:
        return {
            "required_external": self.required_external,
            "signed_external": self.signed_external,
            "internal_signatures": self.internal_signatures,
            "missing_external": self.missing_external,
            "all_signed": self.all_signed,
        }


async def signature_progress(db: AsyncSession, agreement_id: uuid.UUID) -> SignatureProgress:
    """Compute who still has to sign."""
    parties_result = await db.execute(
        select(ExternalParty).where(ExternalParty.agreement_id == agreement_id)
    )
    parties = [
        p
        for p in parties_result.scalars().all()
        if getattr(p, "can_sign", True) and p.status not in _NON_SIGNING_EXTERNAL_STATUSES
    ]

    ext_result = await db.execute(
        select(ExternalPartySignature.external_party_id).where(
            ExternalPartySignature.agreement_id == agreement_id
        )
    )
    signed_party_ids = {row[0] for row in ext_result.all()}

    int_result = await db.execute(
        select(InternalSignature.id).where(InternalSignature.agreement_id == agreement_id)
    )
    internal_count = len(int_result.all())

    missing = [p.signatory_email for p in parties if p.id not in signed_party_ids]
    return SignatureProgress(
        required_external=len(parties),
        signed_external=len([p for p in parties if p.id in signed_party_ids]),
        internal_signatures=internal_count,
        missing_external=missing,
    )


class ExecutionBlocked(Exception):
    """Raised when the lifecycle refuses the execute/partial_sign move."""


async def check_and_execute(
    db: AsyncSession,
    *,
    agreement: Agreement,
    org_id: uuid.UUID,
    provider: ESignatureProvider | None = None,
) -> bool:
    """Advance the agreement after a signature was recorded.

    * all required signers done  → ``execute`` (locks the final version,
      stamps ``execution_date`` and records an EXECUTED audit event);
    * some signers still missing → ``partial_sign`` (SIGNING →
      PARTIALLY_SIGNED, or stays PARTIALLY_SIGNED).

    Returns True when the agreement became executed.
    """
    if agreement.status in IMMUTABLE_STATES:
        return False

    progress = await signature_progress(db, agreement.id)
    if progress.total_signatures < 1:
        return False

    if not progress.all_signed:
        if agreement.status in (AgreementStatus.SIGNING, AgreementStatus.PARTIALLY_SIGNED):
            try:
                await apply_transition(
                    db,
                    agreement=agreement,
                    action_key="partial_sign",
                    actor_id=_SYSTEM_ACTOR_ID,
                    org_id=org_id,
                    actor_type="system",
                    metadata_json=progress.to_dict(),
                )
            except TransitionNotAllowed:
                # Org/type overrides may not model the sub-state; the
                # agreement simply stays in SIGNING.
                pass
        return False

    try:
        await apply_transition(
            db,
            agreement=agreement,
            action_key="execute",
            actor_id=_SYSTEM_ACTOR_ID,
            org_id=org_id,
            actor_type="system",
            metadata_json=progress.to_dict(),
        )
    except TransitionNotAllowed as exc:
        raise ExecutionBlocked(str(exc)) from exc

    agreement.execution_date = datetime.now(timezone.utc).date()

    version = await get_latest_version(db, agreement.id)
    if version:
        await lock_version(db, version)
        if version.content:
            document_hash = hashlib.sha256(version.content.encode()).hexdigest()
            version.content_hash = document_hash
            await db.flush()
            await record_event(
                db,
                tenant_id=org_id,
                agreement_id=agreement.id,
                actor_id=None,
                actor_type="system",
                action="EXECUTED",
                resource_type="agreement",
                resource_id=agreement.id,
                metadata_json={
                    "document_hash": document_hash,
                    **progress.to_dict(),
                },
            )

    await db.flush()
    
    # Check if immovable (requires special form)
    from app.models.execution import ExecutionRequirement
    reqs = await db.execute(
        select(ExecutionRequirement).where(
            (ExecutionRequirement.agreement_id == agreement.id) | 
            (ExecutionRequirement.agreement_type_id == agreement.agreement_type_id),
            ExecutionRequirement.requirement_type.in_(["notarization", "witness"])
        )
    )
    is_immovable = reqs.first() is not None

    if is_immovable:
        from app.services.special_form_service import create_special_form_record
        await create_special_form_record(db, agreement.id)
        # Skip sealing via eSign provider
        return True

    # Seal via the eSign provider when the agreement has a provider envelope;
    # seal() resolves the configured provider and no-ops without one.
    await seal(db, agreement, provider, org_id)

    return True

async def _resolve_envelope_id(db: AsyncSession, agreement: Agreement) -> str | None:
    """Find the provider envelope id for an agreement.

    Prefer explicit agreement columns when present, then fall back to the
    ``provider_envelope_id`` recorded on the agreement's signature requests
    when envelopes were created via /esignature/envelope (spec 24.5).
    """
    explicit = getattr(agreement, "envelope_id", None) or getattr(
        agreement, "esignature_envelope_id", None
    )
    if explicit:
        return str(explicit)

    from app.models.execution import SignatureRequest

    result = await db.execute(
        select(SignatureRequest)
        .where(SignatureRequest.agreement_id == agreement.id)
        .order_by(SignatureRequest.created_at.desc())
    )
    for req in result.scalars():
        envelope_id = (req.metadata_json or {}).get("provider_envelope_id")
        if envelope_id:
            return str(envelope_id)
    return None


async def seal(
    db: AsyncSession,
    agreement: Agreement,
    provider: ESignatureProvider | None,
    org_id: uuid.UUID
) -> None:
    """Download signed PDF, upload to Object Storage, update agreement."""
    from app.services.storage_service import StorageService
    from app.services.esignature import resolve_esignature_provider

    envelope_id = await _resolve_envelope_id(db, agreement)
    if not envelope_id:
        return

    if provider is None:
        provider = resolve_esignature_provider()

    # Download signed PDF
    pdf_bytes = await provider.download_signed_document(envelope_id)

    # Initialize storage service
    storage = StorageService(db)

    key = f"agreements/{org_id}/{agreement.id}/signed_{envelope_id}.pdf"
    
    # Upload and get StoredObject
    stored_obj = await storage.upload_document(
        agreement_id=agreement.id,
        file_data=pdf_bytes,
        key=key,
        content_type="application/pdf",
        uploaded_by=None
    )

    agreement.sealed_document_key = stored_obj.key
    agreement.sealed_at = datetime.now(timezone.utc)
    await db.flush()


async def cancel_signing(
    db: AsyncSession,
    agreement: Agreement,
    org_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
    provider: ESignatureProvider | None = None,
    reason: str = "Signing cancelled",
) -> None:
    from app.services.esignature import resolve_esignature_provider

    envelope_id = await _resolve_envelope_id(db, agreement)
    if envelope_id:
        if provider is None:
            provider = resolve_esignature_provider()
        await provider.void_envelope(envelope_id, reason)
    
    await apply_transition(
        db,
        agreement=agreement,
        action_key="cancel",
        actor_id=actor_id if actor_id is not None else _SYSTEM_ACTOR_ID,
        org_id=org_id,
        actor_type="user" if actor_id else "system",
        metadata_json={"reason": reason}
    )
    await db.flush()

async def decline_signing(
    db: AsyncSession,
    agreement: Agreement,
    org_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
    provider: ESignatureProvider | None = None,
    reason: str = "Signing declined by counterparty",
) -> None:
    from app.services.esignature import resolve_esignature_provider

    envelope_id = await _resolve_envelope_id(db, agreement)
    if envelope_id:
        if provider is None:
            provider = resolve_esignature_provider()
        await provider.void_envelope(envelope_id, reason)

    await apply_transition(
        db,
        agreement=agreement,
        action_key="decline",
        actor_id=actor_id if actor_id is not None else _SYSTEM_ACTOR_ID,
        org_id=org_id,
        actor_type="user" if actor_id else "system",
        metadata_json={"reason": reason}
    )
    await db.flush()
