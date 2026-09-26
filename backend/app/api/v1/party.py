"""Party master-data API (spec §3.4.14, §3.4.43).

Contacts / addresses / identifiers CRUD, duplicate detection, guarded merge,
and unified party search feeding the global search endpoint.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.legal_entity import LegalEntity
from app.models.party import Address, Contact, PartyIdentifier
from app.models.user import User
from app.services import party_service

router = APIRouter(prefix="/parties", tags=["Party Master Data"])


class ContactCreate(BaseModel):
    legal_entity_id: str
    name: str
    title: str | None = None
    email: str | None = None
    phone: str | None = None
    is_primary: bool = False
    notes: str | None = None


class AddressCreate(BaseModel):
    address_type: str = Field(..., pattern="^(registered|billing|shipping|visit)$")
    line1: str
    line2: str | None = None
    city: str | None = None
    region: str | None = None
    postal_code: str | None = None
    country: str | None = Field(default=None, min_length=2, max_length=2)
    legal_entity_id: str | None = None
    contact_id: str | None = None
    is_primary: bool = False


class IdentifierCreate(BaseModel):
    legal_entity_id: str
    identifier_type: str = Field(
        ..., pattern="^(company_registration|tax_id|vat|duns|lei|other)$"
    )
    value: str
    issuing_country: str | None = Field(default=None, min_length=2, max_length=2)


class MergeRequest(BaseModel):
    primary_id: str
    duplicate_id: str


@router.get("/contacts")
async def list_contacts(
    legal_entity_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    stmt = select(Contact).where(Contact.organization_id == uuid.UUID(str(org_id)))
    if legal_entity_id:
        stmt = stmt.where(Contact.legal_entity_id == uuid.UUID(legal_entity_id))
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": str(c.id),
            "legal_entity_id": str(c.legal_entity_id),
            "name": c.name,
            "title": c.title,
            "email": c.email,
            "phone": c.phone,
            "is_primary": c.is_primary,
            "status": c.status,
        }
        for c in rows
    ]


@router.post("/contacts", status_code=201)
async def create_contact(
    body: ContactCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    entity = await db.get(LegalEntity, uuid.UUID(body.legal_entity_id))
    if entity is None or entity.organization_id != uuid.UUID(str(org_id)):
        raise HTTPException(status_code=404, detail="Legal entity not found")
    contact = await party_service.create_contact(
        db,
        organization_id=uuid.UUID(str(org_id)),
        legal_entity_id=entity.id,
        name=body.name,
        title=body.title,
        email=body.email,
        phone=body.phone,
        is_primary=body.is_primary,
        notes=body.notes,
    )
    await db.commit()
    return {"id": str(contact.id), "name": contact.name}


@router.post("/addresses", status_code=201)
async def create_address(
    body: AddressCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    try:
        address = await party_service.create_address(
            db,
            organization_id=uuid.UUID(str(org_id)),
            address_type=body.address_type,
            line1=body.line1,
            legal_entity_id=(
                uuid.UUID(body.legal_entity_id) if body.legal_entity_id else None
            ),
            contact_id=uuid.UUID(body.contact_id) if body.contact_id else None,
            line2=body.line2,
            city=body.city,
            region=body.region,
            postal_code=body.postal_code,
            country=body.country,
            is_primary=body.is_primary,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    return {"id": str(address.id)}


@router.post("/identifiers", status_code=201)
async def add_identifier(
    body: IdentifierCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    identifier = await party_service.add_identifier(
        db,
        organization_id=uuid.UUID(str(org_id)),
        legal_entity_id=uuid.UUID(body.legal_entity_id),
        identifier_type=body.identifier_type,
        value=body.value,
        issuing_country=body.issuing_country,
    )
    await db.commit()
    return {"id": str(identifier.id)}


@router.post("/duplicates/check")
async def check_duplicates(
    legal_name: str,
    email: str | None = None,
    registration_number: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    """Deterministic + similarity duplicate detection (§3.4.26-27)."""
    return await party_service.find_duplicates(
        db,
        organization_id=uuid.UUID(str(org_id)),
        legal_name=legal_name,
        email=email,
        registration_number=registration_number,
    )


@router.post("/merge")
async def merge_parties(
    body: MergeRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    """Merge a duplicate entity into the primary (§3.4.28-29)."""
    try:
        result = await party_service.merge_entities(
            db,
            organization_id=uuid.UUID(str(org_id)),
            primary_id=uuid.UUID(body.primary_id),
            duplicate_id=uuid.UUID(body.duplicate_id),
            merged_by=user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()
    return result


@router.get("/search")
async def search_parties(
    q: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    org_id: str = Depends(get_current_organization_id),
):
    """Unified party search: name, identifier value, contact email."""
    return await party_service.search_parties(
        db, organization_id=uuid.UUID(str(org_id)), query=q
    )
