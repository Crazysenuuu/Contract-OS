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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agreement_states import IMMUTABLE_STATES, AgreementStatus
from app.models.agreement import Agreement
from app.models.external_party import ExternalParty, ExternalPartySignature
from app.models.signature import InternalSignature
from app.services.agreement_versioning import get_latest_version, lock_version
from app.services.audit_service import record_event
from app.services.lifecycle_service import TransitionNotAllowed, apply_transition

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
                    actor_id=None,
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
            actor_id=None,
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
    return True
