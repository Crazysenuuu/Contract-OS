"""Server-side agreement context builder (spec 1.8.13-1.8.15).

Constructs the clause-rendering context from real database entities. The
frontend never supplies authoritative legal facts; party identities come
from AgreementParty -> LegalEntity records, keyed by role.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import Agreement
from app.models.agreement_access import AgreementParty
from app.models.legal_entity import LegalEntity


class AgreementContextBuilder:
    """Builds the authoritative rendering context for an agreement."""

    async def build(self, db: AsyncSession, agreement: Agreement) -> dict:
        result = await db.execute(
            select(AgreementParty, LegalEntity)
            .join(LegalEntity, LegalEntity.id == AgreementParty.legal_entity_id)
            .where(AgreementParty.agreement_id == agreement.id)
        )
        parties = result.all()

        parties_by_role: dict[str, dict] = {}
        parties_list: list[dict] = []
        for party, entity in parties:
            entry = {
                "party_id": str(party.id),
                "role": party.party_role,
                "legal_entity_id": str(entity.id),
                "legal_name": entity.legal_name,
                "registration_number": entity.registration_number,
                "registered_address": entity.registered_address,
                "tax_identifier": entity.tax_identifier,
                "country": entity.country,
                "entity_type": entity.entity_type,
            }
            parties_list.append(entry)
            # Role-keyed aliases (buyer, seller, disclosing_party, ...) are
            # derived from actual AgreementParty rows - never from the client.
            role_key = (party.party_role or "").strip().lower().replace(" ", "_")
            if role_key:
                parties_by_role.setdefault(role_key, entry)

        context = {
            "agreement": {
                "id": str(agreement.id),
                # Business reference (spec 2.01 §46); templates may render it.
                "agreement_number": getattr(agreement, "agreement_number", None),
                "title": agreement.title,
                "effective_date": (
                    agreement.effective_date.isoformat()
                    if agreement.effective_date
                    else None
                ),
                "execution_date": (
                    agreement.execution_date.isoformat()
                    if agreement.execution_date
                    else None
                ),
                "expiry_date": (
                    agreement.expiry_date.isoformat()
                    if agreement.expiry_date
                    else None
                ),
                "governing_law": getattr(agreement, "governing_law", None),
            },
            "parties": parties_list,
            "parties_by_role": parties_by_role,
        }

        # Convenience aliases party_a / party_b, assigned only from real
        # party records in stable role order (spec 1.8.15).
        ordered = sorted(
            parties_list, key=lambda p: str(p.get("party_id") or "")
        )
        if len(ordered) > 0:
            context["party_a"] = ordered[0]
        if len(ordered) > 1:
            context["party_b"] = ordered[1]

        return context
