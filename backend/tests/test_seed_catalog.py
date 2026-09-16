"""Tests for the extended agreement-type catalog seeding (E1).

Validates schema composition, idempotent upsert, and exposure through the
agreement-types API.
"""
import pytest
from sqlalchemy import func, select

from app.models.agreement_type import AgreementType
from app.models.jurisdiction import Jurisdiction, JurisdictionClause

from seed_data import (
    EXTENDED_AGREEMENT_TYPES,
    catalog_type_id,
    compose_catalog_schema,
    seed_additional_jurisdictions,
    seed_catalog_agreement_types,
)


@pytest.fixture
def agreements_api_path():
    return "/api/v1/agreements/types"


class TestCatalogSeed:
    async def test_compact_catalog_valid(self):
        assert len(EXTENDED_AGREEMENT_TYPES) > 40
        keys = [t[0] for t in EXTENDED_AGREEMENT_TYPES]
        assert len(set(keys)) == len(keys)
        for key, name, category, kind, desc, extras in EXTENDED_AGREEMENT_TYPES:
            schema = compose_catalog_schema(kind, extras)
            assert schema["questions"] and schema["clauses"]
            assert all(q.get("key") and q.get("label") for q in schema["questions"])

    async def test_seed_creates_all_types(self, db_session):
        await seed_catalog_agreement_types(db_session)
        await db_session.commit()

        result = await db_session.execute(select(func.count()).select_from(AgreementType))
        assert result.scalar_one() == len(EXTENDED_AGREEMENT_TYPES)

        result = await db_session.execute(
            select(AgreementType).where(AgreementType.key == "loan_agreement")
        )
        loan = result.scalar_one()
        assert loan.template_key == "generic_agreement_lk_v1"
        assert any(
            q.get("key") == "principal_amount" for q in loan.schema["questions"]
        )
        assert "repayment_schedule" in loan.schema["clauses"]

    async def test_seed_includes_corporate_governance_types(self, db_session):
        """Spec §3.A: the whole Corporate & Governance category must exist."""
        await seed_catalog_agreement_types(db_session)
        await db_session.commit()

        result = await db_session.execute(
            select(AgreementType.key).where(
                AgreementType.key.in_(
                    [
                        "shareholders_agreement",
                        "share_purchase",
                        "investment_agreement",
                        "convertible_note",
                        "safe_investment",
                        "founder_agreement",
                        "board_resolution",
                    ]
                )
            )
        )
        assert {row[0] for row in result.all()} == {
            "shareholders_agreement",
            "share_purchase",
            "investment_agreement",
            "convertible_note",
            "safe_investment",
            "founder_agreement",
            "board_resolution",
        }

    async def test_seed_includes_commercial_types(self, db_session):
        """Spec §11-20: SLA, purchase, distribution, reseller, referral, commission."""
        await seed_catalog_agreement_types(db_session)
        await db_session.commit()

        result = await db_session.execute(
            select(AgreementType.key).where(
                AgreementType.key.in_(
                    [
                        "service_level_agreement",
                        "purchase_agreement",
                        "distribution",
                        "reseller",
                        "referral",
                        "commission",
                    ]
                )
            )
        )
        assert len(result.all()) == 6

    async def test_sla_type_has_measurable_questions(self, db_session):
        """Spec §11: an SLA type carries monitorable service-level fields."""
        await seed_catalog_agreement_types(db_session)
        await db_session.commit()

        result = await db_session.execute(
            select(AgreementType).where(AgreementType.key == "service_level_agreement")
        )
        sla = result.scalar_one()
        keys = {q["key"] for q in sla.schema["questions"]}
        assert {"uptime_target", "response_time_hours", "service_credits"}.issubset(keys)
        assert "service_levels" in sla.schema["clauses"]

    async def test_board_resolution_gets_dedicated_template(self, db_session):
        """Spec §3.A.7: a Board Resolution is a corporate record, not a
        bilateral agreement, so it must not use the generic party template."""
        await seed_catalog_agreement_types(db_session)
        await db_session.commit()

        result = await db_session.execute(
            select(AgreementType).where(AgreementType.key == "board_resolution")
        )
        br = result.scalar_one()
        assert br.template_key == "board_resolution_lk_v1"
        assert any(q["key"] == "resolution_text" for q in br.schema["questions"])

    async def test_seed_uses_deterministic_ids(self, db_session):
        await seed_catalog_agreement_types(db_session)
        await db_session.commit()

        result = await db_session.execute(
            select(AgreementType.id).where(AgreementType.key == "offer_letter")
        )
        assert result.scalar_one() == catalog_type_id("offer_letter")

    async def test_seed_idempotent(self, db_session):
        await seed_catalog_agreement_types(db_session)
        await db_session.commit()
        await seed_catalog_agreement_types(db_session)
        await db_session.commit()

        result = await db_session.execute(select(func.count()).select_from(AgreementType))
        assert result.scalar_one() == len(EXTENDED_AGREEMENT_TYPES)

    async def test_types_exposed_via_api(
        self, client, auth_headers, db_session, agreements_api_path
    ):
        await seed_catalog_agreement_types(db_session)
        await db_session.commit()

        resp = await client.get(agreements_api_path, headers=auth_headers)
        assert resp.status_code == 200
        types = resp.json()
        assert len(types) >= len(EXTENDED_AGREEMENT_TYPES)
        assert any(t["key"] == "logistics" for t in types)
        loan = next(t for t in types if t["key"] == "loan_agreement")
        assert loan["template_key"] == "generic_agreement_lk_v1"
        assert loan["schema"]["questions"]


class TestAdditionalJurisdictions:
    async def test_seeds_extra_jurisdictions(self, db_session):
        await seed_additional_jurisdictions(db_session)
        await db_session.commit()

        result = await db_session.execute(
            select(Jurisdiction).where(Jurisdiction.code.in_(["AU", "AE"]))
        )
        rows = result.scalars().all()
        assert {r.code for r in rows} == {"AU", "AE"}

        jc = await db_session.execute(
            select(JurisdictionClause).where(
                JurisdictionClause.jurisdiction_id.in_([r.id for r in rows])
            )
        )
        clause_rows = jc.scalars().all()
        types = {c.clause_type for c in clause_rows}
        assert {"governing_law", "dispute_resolution"}.issubset(types)

    async def test_seeds_idempotent(self, db_session):
        await seed_additional_jurisdictions(db_session)
        await db_session.commit()
        await seed_additional_jurisdictions(db_session)
        await db_session.commit()

        result = await db_session.execute(select(func.count()).select_from(Jurisdiction))
        assert result.scalar_one() == 2