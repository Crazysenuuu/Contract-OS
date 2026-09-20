import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.special_form import SpecialFormRecord
from app.models.agreement import Agreement


async def create_special_form_record(
    db: AsyncSession,
    agreement_id: uuid.UUID
) -> SpecialFormRecord:
    """Create a special form record for immovable agreements."""
    record = SpecialFormRecord(
        agreement_id=agreement_id,
        status="PENDING"
    )
    db.add(record)
    await db.flush()
    return record


async def get_special_form_record(
    db: AsyncSession,
    agreement_id: uuid.UUID
) -> SpecialFormRecord | None:
    res = await db.execute(
        select(SpecialFormRecord).where(SpecialFormRecord.agreement_id == agreement_id)
    )
    return res.scalar_one_or_none()


async def verify_special_form(
    db: AsyncSession,
    agreement: Agreement,
    org_id: uuid.UUID,
    notary_name: str,
    uploaded_scan_key: str | None = None,
    actor_id: uuid.UUID | None = None,
) -> SpecialFormRecord:
    """Verify a special form record and transition the agreement to EXECUTED."""
    record = await get_special_form_record(db, agreement.id)
    if not record:
        record = await create_special_form_record(db, agreement.id)

    record.status = "VERIFIED"
    record.notary_or_witness_name = notary_name
    record.uploaded_scan_key = uploaded_scan_key

    # Transition agreement to EXECUTED via lifecycle service
    from app.services.lifecycle_service import apply_transition, TransitionNotAllowed

    _SYSTEM_ACTOR_ID = uuid.UUID(int=0)
    try:
        await apply_transition(
            db,
            agreement=agreement,
            action_key="execute",
            actor_id=actor_id if actor_id is not None else _SYSTEM_ACTOR_ID,
            org_id=org_id,
            actor_type="user" if actor_id else "system",
            metadata_json={"source": "special_form", "notary": notary_name},
        )
    except TransitionNotAllowed:
        # Already executed or transition not applicable — safe to ignore
        pass

    await db.flush()
    return record
