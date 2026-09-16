"""Contract repository search (spec 2.13).

PostgreSQL full-text search over agreement metadata (title, governing law,
party names, agreement type) with metadata-driven filters and saved
searches. Falls back to substring matching on SQLite so the test suite can
exercise the same endpoint.

Per spec 2.07.30 the filters are driven by real metadata and an empty
dataset returns "no matching agreements" — never fabricated statistics.
"""

from __future__ import annotations

import time
import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.agreement import Agreement
from app.models.agreement_access import AgreementParty
from app.models.agreement_type import AgreementType
from app.models.legal_entity import LegalEntity
from app.models.saved_search import SavedSearch
from app.models.user import User

router = APIRouter(prefix="/search", tags=["search"])


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------


class SearchResultItem(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    agreement_type_id: uuid.UUID
    agreement_type_name: str | None = None
    party_names: list[str] = Field(default_factory=list)
    governing_law: str | None = None
    effective_date: date | None = None
    execution_date: date | None = None
    expiry_date: date | None = None
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime


class SearchResponse(BaseModel):
    items: list[SearchResultItem]
    total: int
    took_ms: int | None = None


class SavedSearchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    query: str | None = Field(default=None, max_length=500)
    filters: dict = Field(default_factory=dict)
    is_favorite: bool = False


class SavedSearchUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    query: str | None = Field(default=None, max_length=500)
    filters: dict | None = None
    is_favorite: bool | None = None


class SavedSearchResponse(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    created_by: uuid.UUID
    name: str
    query: str | None
    filters: dict
    is_favorite: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# --------------------------------------------------------------------------
# Query building
# --------------------------------------------------------------------------


def _search_text_condition(q: str, dialect: str):
    """Full-text condition for the free-text query.

    PostgreSQL uses real FTS (spec 2.07.29) with websearch syntax; SQLite
    falls back to case-insensitive substring matching across the same
    searchable fields.
    """
    if dialect == "postgresql":
        vector = func.to_tsvector(
            "simple",
            func.concat(
                Agreement.title,
                " ",
                func.coalesce(Agreement.governing_law, ""),
            ),
        )
        text_match = vector.op("@@")(func.websearch_to_tsquery("simple", q))
    else:
        like = f"%{q}%"
        text_match = or_(
            Agreement.title.ilike(like),
            func.coalesce(Agreement.governing_law, "").ilike(like),
        )

    # Party display names are the primary label; legal entity names enrich.
    party_match = (
        select(1)
        .select_from(AgreementParty)
        .join(LegalEntity, LegalEntity.id == AgreementParty.legal_entity_id)
        .where(
            AgreementParty.agreement_id == Agreement.id,
            or_(
                func.coalesce(AgreementParty.display_name, "").ilike(f"%{q}%"),
                func.coalesce(LegalEntity.legal_name, "").ilike(f"%{q}%"),
            ),
        )
    )
    type_match = select(1).where(
        AgreementType.id == Agreement.agreement_type_id,
        AgreementType.name.ilike(f"%{q}%"),
    )

    return or_(
        text_match,
        party_match.exists(),
        type_match.exists(),
    )


def _party_filter_condition(party: str):
    """Agreements with a party whose name contains ``party``."""
    match = (
        select(1)
        .select_from(AgreementParty)
        .join(LegalEntity, LegalEntity.id == AgreementParty.legal_entity_id)
        .where(
            AgreementParty.agreement_id == Agreement.id,
            or_(
                func.coalesce(AgreementParty.display_name, "").ilike(f"%{party}%"),
                func.coalesce(LegalEntity.legal_name, "").ilike(f"%{party}%"),
            ),
        )
    )
    return match.exists()


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------


@router.get("/agreements", response_model=SearchResponse)
async def search_agreements(
    q: str | None = Query(default=None, max_length=500),
    status_filter: str | None = Query(default=None, alias="status"),
    agreement_type_id: uuid.UUID | None = None,
    party: str | None = Query(default=None, max_length=500),
    effective_from: date | None = None,
    effective_to: date | None = None,
    expiry_from: date | None = None,
    expiry_to: date | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    """Search the contract repository (spec 2.07.29/2.07.30)."""
    started = time.perf_counter()
    dialect = db.get_bind().dialect.name

    conditions = [Agreement.organization_id == org_id]
    if q:
        conditions.append(_search_text_condition(q, dialect))
    if status_filter:
        conditions.append(Agreement.status == status_filter)
    if agreement_type_id:
        conditions.append(Agreement.agreement_type_id == agreement_type_id)
    if party:
        conditions.append(_party_filter_condition(party))
    if effective_from:
        conditions.append(Agreement.effective_date >= effective_from)
    if effective_to:
        conditions.append(Agreement.effective_date <= effective_to)
    if expiry_from:
        conditions.append(Agreement.expiry_date >= expiry_from)
    if expiry_to:
        conditions.append(Agreement.expiry_date <= expiry_to)

    base = select(Agreement).where(and_(*conditions))

    total_result = await db.execute(
        select(func.count()).select_from(base.subquery())
    )
    total = total_result.scalar() or 0

    result = await db.execute(
        base.order_by(Agreement.created_at.desc()).offset(offset).limit(limit)
    )
    agreements = list(result.scalars().all())
    if not agreements:
        return SearchResponse(items=[], total=total, took_ms=0)

    # Enrich with agreement type names + party names.
    agreement_ids = [a.id for a in agreements]
    type_ids = {a.agreement_type_id for a in agreements}

    types_result = await db.execute(
        select(AgreementType.id, AgreementType.name).where(
            AgreementType.id.in_(type_ids)
        )
    )
    type_names = {row[0]: row[1] for row in types_result.all()}

    parties_result = await db.execute(
        select(
            AgreementParty.agreement_id,
            func.coalesce(AgreementParty.display_name, LegalEntity.legal_name).label("party_name"),
        )
        .join(LegalEntity, LegalEntity.id == AgreementParty.legal_entity_id)
        .where(AgreementParty.agreement_id.in_(agreement_ids))
        .order_by(AgreementParty.agreement_id)
    )
    party_names: dict[uuid.UUID, list[str]] = {}
    for agreement_id, name in parties_result.all():
        party_names.setdefault(agreement_id, []).append(name)

    items = [
        SearchResultItem(
            id=a.id,
            title=a.title,
            status=a.status,
            agreement_type_id=a.agreement_type_id,
            agreement_type_name=type_names.get(a.agreement_type_id),
            party_names=party_names.get(a.id, []),
            governing_law=a.governing_law,
            effective_date=a.effective_date,
            execution_date=a.execution_date,
            expiry_date=a.expiry_date,
            created_by=a.created_by,
            created_at=a.created_at,
            updated_at=a.updated_at,
        )
        for a in agreements
    ]
    took_ms = int((time.perf_counter() - started) * 1000)
    return SearchResponse(items=items, total=total, took_ms=took_ms)


# --- Saved searches --------------------------------------------------------


@router.get("/saved", response_model=list[SavedSearchResponse])
async def list_saved_searches(
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SavedSearch)
        .where(SavedSearch.organization_id == org_id)
        .order_by(
            SavedSearch.is_favorite.desc(),
            SavedSearch.created_at.desc(),
        )
    )
    return result.scalars().all()


@router.post(
    "/saved",
    response_model=SavedSearchResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_saved_search(
    data: SavedSearchCreate,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    saved = SavedSearch(
        organization_id=org_id,
        created_by=current_user.id,
        name=data.name,
        query=data.query,
        filters=data.filters,
        is_favorite=data.is_favorite,
    )
    db.add(saved)
    await db.flush()
    await db.refresh(saved)
    return saved


@router.patch(
    "/saved/{saved_id}",
    response_model=SavedSearchResponse,
)
async def update_saved_search(
    saved_id: uuid.UUID,
    data: SavedSearchUpdate,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SavedSearch).where(
            SavedSearch.id == saved_id,
            SavedSearch.organization_id == org_id,
        )
    )
    saved = result.scalar_one_or_none()
    if saved is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Saved search not found",
        )
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(saved, field, value)
    await db.flush()
    await db.refresh(saved)
    return saved


@router.delete("/saved/{saved_id}")
async def delete_saved_search(
    saved_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SavedSearch).where(
            SavedSearch.id == saved_id,
            SavedSearch.organization_id == org_id,
        )
    )
    saved = result.scalar_one_or_none()
    if saved is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Saved search not found",
        )
    await db.delete(saved)
    await db.flush()
    return {"deleted": True, "id": str(saved_id)}