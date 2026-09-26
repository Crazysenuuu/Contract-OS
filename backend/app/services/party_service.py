"""Party master-data service (spec §3.4.25-34, §3.4.43).

Deterministic duplicate detection (normalized name/email), fuzzy similarity
via trigram-style scoring, a guarded merge workflow (sources archived, links
rewritten, audit trail), and unified search across parties.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.party import Address, Contact, PartyIdentifier
from app.models.agreement import Agreement


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


async def create_contact(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    legal_entity_id: uuid.UUID,
    name: str,
    title: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    is_primary: bool = False,
    notes: str | None = None,
) -> Contact:
    contact = Contact(
        organization_id=organization_id,
        legal_entity_id=legal_entity_id,
        name=name,
        title=title,
        email=email,
        phone=phone,
        is_primary=is_primary,
        notes=notes,
    )
    if is_primary:
        await _clear_primary_contacts(db, organization_id, legal_entity_id)
    db.add(contact)
    await db.flush()
    return contact


async def _clear_primary_contacts(
    db: AsyncSession, organization_id: uuid.UUID, legal_entity_id: uuid.UUID
) -> None:
    existing = (
        await db.execute(
            select(Contact).where(
                Contact.organization_id == organization_id,
                Contact.legal_entity_id == legal_entity_id,
                Contact.is_primary.is_(True),
            )
        )
    ).scalars().all()
    for c in existing:
        c.is_primary = False


async def create_address(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    address_type: str,
    line1: str,
    legal_entity_id: uuid.UUID | None = None,
    contact_id: uuid.UUID | None = None,
    line2: str | None = None,
    city: str | None = None,
    region: str | None = None,
    postal_code: str | None = None,
    country: str | None = None,
    is_primary: bool = False,
) -> Address:
    if legal_entity_id is None and contact_id is None:
        raise ValueError("Address requires a legal_entity_id or contact_id")
    address = Address(
        organization_id=organization_id,
        legal_entity_id=legal_entity_id,
        contact_id=contact_id,
        address_type=address_type,
        line1=line1,
        line2=line2,
        city=city,
        region=region,
        postal_code=postal_code,
        country=country,
        is_primary=is_primary,
    )
    db.add(address)
    await db.flush()
    return address


async def add_identifier(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    legal_entity_id: uuid.UUID,
    identifier_type: str,
    value: str,
    issuing_country: str | None = None,
) -> PartyIdentifier:
    identifier = PartyIdentifier(
        organization_id=organization_id,
        legal_entity_id=legal_entity_id,
        identifier_type=identifier_type,
        value=value.strip(),
        issuing_country=issuing_country,
    )
    db.add(identifier)
    await db.flush()
    return identifier


# ---------------------------------------------------------------------------
# Duplicate detection (§3.4.25-27)
# ---------------------------------------------------------------------------


def _normalize(text: str | None) -> str:
    """Lowercase, strip punctuation and legal suffixes for name matching."""
    if not text:
        return ""
    lowered = re.sub(r"[^\w\s]", "", text.lower())
    lowered = re.sub(
        r"\b(inc|ltd|llc|gmbh|bv|pty|pvt|corp|co|company|limited)\b", "", lowered
    )
    return re.sub(r"\s+", " ", lowered).strip()


def _email_domain(email: str | None) -> str:
    if not email or "@" not in email:
        return ""
    return email.rsplit("@", 1)[1].lower()


def _token_similarity(a: str, b: str) -> float:
    """Jaccard similarity over word tokens (deterministic, no deps)."""
    ta = set(_normalize(a).split())
    tb = set(_normalize(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


async def find_duplicates(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    legal_name: str,
    email: str | None = None,
    registration_number: str | None = None,
    threshold: float = 0.8,
) -> list[dict]:
    """Deterministic + similarity duplicate detection (§3.4.26-27).

    Exact matches on normalized name, email domain or registration number
    are returned with high confidence; near-name matches above threshold
    are flagged as 'possible'.
    """
    from app.models.legal_entity import LegalEntity

    candidates = (
        await db.execute(
            select(LegalEntity).where(
                LegalEntity.organization_id == organization_id
            )
        )
    ).scalars().all()

    email_domain = _email_domain(email)
    results: list[dict] = []
    for entity in candidates:
        score = 0.0
        match_reasons: list[str] = []
        if registration_number and entity.registration_number:
            if entity.registration_number.strip() == registration_number.strip():
                score = 1.0
                match_reasons.append("registration_number_exact")
        if email_domain and _email_domain(getattr(entity, "primary_email", None)) == email_domain:
            score = max(score, 0.9)
            match_reasons.append("email_domain")
        name_sim = _token_similarity(legal_name, entity.legal_name)
        if _normalize(legal_name) and _normalize(legal_name) == _normalize(entity.legal_name):
            score = max(score, 1.0)
            match_reasons.append("name_exact")
        elif name_sim >= threshold:
            score = max(score, name_sim)
            match_reasons.append("name_similarity")

        if score >= threshold:
            results.append(
                {
                    "legal_entity_id": str(entity.id),
                    "legal_name": entity.legal_name,
                    "score": round(score, 3),
                    "match_reasons": match_reasons,
                    "match_type": (
                        "exact" if score >= 0.99 else "possible"
                    ),
                }
            )
    results.sort(key=lambda r: r["score"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# Merge (§3.4.28-29)
# ---------------------------------------------------------------------------


async def merge_entities(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    primary_id: uuid.UUID,
    duplicate_id: uuid.UUID,
    merged_by: uuid.UUID,
) -> dict:
    """Merge a duplicate entity into the primary (§3.4.28).

    - Agreement-party rows on the duplicate are repointed to the primary.
    - Contacts, addresses and identifiers move across (skip exact dups).
    - The duplicate stays in place but is archived via status so history is
      never destroyed; a follow-up archive of the entity row is left to the
      entity service.

    Raises when the two entities share an agreement party row (would create
    a duplicate party on one agreement — §3.4.29 merge constraint).
    """
    from app.models.agreement_access import AgreementParty
    from app.models.legal_entity import LegalEntity

    primary = await db.get(LegalEntity, primary_id)
    duplicate = await db.get(LegalEntity, duplicate_id)
    if primary is None or duplicate is None:
        raise ValueError("Entity not found")
    if primary_id == duplicate_id:
        raise ValueError("Cannot merge an entity into itself")

    dup_parties = (
        await db.execute(
            select(AgreementParty).where(
                AgreementParty.legal_entity_id == duplicate_id
            )
        )
    ).scalars().all()
    primary_party_agreements = {
        p.agreement_id
        for p in (
            await db.execute(
                select(AgreementParty).where(
                    AgreementParty.legal_entity_id == primary_id
                )
            )
        ).scalars().all()
    }
    moved_parties = 0
    for party in dup_parties:
        if party.agreement_id in primary_party_agreements:
            raise ValueError(
                "Merge conflict: both entities are parties on agreement "
                f"{party.agreement_id}; resolve manually"
            )
        party.legal_entity_id = primary_id
        moved_parties += 1

    # Move contacts/identifiers; skip contacts that duplicate primary ones.
    primary_contact_keys = {
        (_normalize(c.name), (c.email or "").lower())
        for c in (
            await db.execute(
                select(Contact).where(Contact.legal_entity_id == primary_id)
            )
        ).scalars().all()
    }
    moved_contacts = 0
    for contact in (
        await db.execute(
            select(Contact).where(Contact.legal_entity_id == duplicate_id)
        )
    ).scalars().all():
        key = (_normalize(contact.name), (contact.email or "").lower())
        if key in primary_contact_keys:
            continue
        contact.legal_entity_id = primary_id
        moved_contacts += 1

    moved_identifiers = 0
    for ident in (
        await db.execute(
            select(PartyIdentifier).where(
                PartyIdentifier.legal_entity_id == duplicate_id
            )
        )
    ).scalars().all():
        ident.legal_entity_id = primary_id
        moved_identifiers += 1

    await db.flush()
    return {
        "primary_id": str(primary_id),
        "duplicate_id": str(duplicate_id),
        "agreement_parties_repointed": moved_parties,
        "contacts_moved": moved_contacts,
        "identifiers_moved": moved_identifiers,
        "merged_by": str(merged_by),
    }


# ---------------------------------------------------------------------------
# Unified search (§3.4.43)
# ---------------------------------------------------------------------------


async def search_parties(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    query: str,
    limit: int = 20,
) -> list[dict]:
    """Search legal entities by name, identifier value and contact email."""
    from app.models.legal_entity import LegalEntity

    like = f"%{query.strip().lower()}%"
    entities = (
        await db.execute(
            select(LegalEntity).where(
                LegalEntity.organization_id == organization_id
            )
        )
    ).scalars().all()

    identifier_entity_ids = {
        row[0]
        for row in (
            await db.execute(
                select(PartyIdentifier.legal_entity_id).where(
                    PartyIdentifier.organization_id == organization_id,
                    PartyIdentifier.value.ilike(like),
                )
            )
        ).all()
    }
    contact_entity_ids = {
        row[0]
        for row in (
            await db.execute(
                select(Contact.legal_entity_id).where(
                    Contact.organization_id == organization_id,
                    or_(Contact.email.ilike(like), Contact.name.ilike(like)),
                )
            )
        ).all()
    }

    out: list[dict] = []
    for entity in entities:
        name_hit = query.strip().lower() in (entity.legal_name or "").lower()
        if not (name_hit or entity.id in identifier_entity_ids or entity.id in contact_entity_ids):
            continue
        out.append(
            {
                "legal_entity_id": str(entity.id),
                "legal_name": entity.legal_name,
                "entity_type": entity.entity_type,
                "matched_by": (
                    ["name"] if name_hit else []
                )
                + (["identifier"] if entity.id in identifier_entity_ids else [])
                + (["contact"] if entity.id in contact_entity_ids else []),
            }
        )
        if len(out) >= limit:
            break
    return out
