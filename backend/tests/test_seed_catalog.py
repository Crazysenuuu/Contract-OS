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
        assert loan.template_key == "financial_agreement_lk_v1"
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

    async def test_catalog_templates_render_for_all_kinds(self, db_session):
        """Every catalog type must resolve to a real contract template on disk,
        so the rendering pipeline never falls back to a missing template."""
        import os
        from pathlib import Path

        template_dir = os.path.join(
            os.path.dirname(__file__), "..", "templates"
        )
        await seed_catalog_agreement_types(db_session)
        await db_session.commit()

        result = await db_session.execute(select(AgreementType))
        for at in result.scalars().all():
            assert at.template_key, f"{at.key}: no template_key"
            template_file = Path(template_dir) / f"{at.template_key}.jinja2"
            assert template_file.exists(), (
                f"{at.key}: mapped to missing template {at.template_key}"
            )

    async def test_backfill_repoints_generic_template(self, db_session):
        """Legacy rows that used the generic fallback get repointed to their
        kind template on the next seed run."""
        from app.models.agreement_type import AgreementType

        legacy = AgreementType(
            id=catalog_type_id("loan_agreement"),
            key="loan_agreement",
            name="Legacy Loan",
            category="Finance",
            status="active",
            version=1,
            schema={"questions": [], "clauses": []},
            template_key="generic_agreement_lk_v1",
        )
        db_session.add(legacy)
        await db_session.commit()

        await seed_catalog_agreement_types(db_session)
        await db_session.commit()
        row = (
            await db_session.execute(
                select(AgreementType).where(AgreementType.key == "loan_agreement")
            )
        ).scalar_one()
        assert row.template_key == "financial_agreement_lk_v1"

    async def test_seed_idempotent(self, db_session):
        await seed_catalog_agreement_types(db_session)
        await db_session.commit()
        await seed_catalog_agreement_types(db_session)
        await db_session.commit()

        result = await db_session.execute(select(func.count()).select_from(AgreementType))
        assert result.scalar_one() == len(EXTENDED_AGREEMENT_TYPES)

    async def test_backfills_schema_for_catalog_key_without_questions(self, db_session):
        """A DB seeded before the catalog existed can hold a catalog-keyed row
        whose schema predates the questionnaire bank. The wizard served it a
        404 on /types/{id}/questions — the next seed run must backfill the
        composed schema."""
        from app.models.agreement_type import AgreementType

        stale = AgreementType(
            id=catalog_type_id("referral"),
            key="referral",
            name="Referral Agreement",
            category="commercial",
            status="active",
            version=1,
            schema={"clauses": []},  # pre-catalog layout: no questions
            template_key="marketing_agreement_lk_v1",
        )
        db_session.add(stale)
        await db_session.commit()

        await seed_catalog_agreement_types(db_session)
        await db_session.commit()
        row = (
            await db_session.execute(
                select(AgreementType).where(AgreementType.key == "referral")
            )
        ).scalar_one()
        assert row.schema.get("questions"), "schema must be backfilled"
        assert any(q["key"] == "commission_rate" for q in row.schema["questions"])
        assert row.status == "active"

    async def test_retires_non_catalog_row_without_questions(self, db_session):
        """Legacy *_agreement duplicate rows (same type, different key) have
        no questionnaire and 404 in the wizard; they must be retired so the
        type list shows exactly one card per agreement type."""
        from app.models.agreement_type import AgreementType

        legacy_dup = AgreementType(
            id=catalog_type_id("legacy_referral"),
            key="referral_agreement",  # not in the catalog
            name="Referral Agreement",
            category="Commercial",
            status="active",
            version=1,
            schema={"clauses": []},
            template_key="generic_agreement_lk_v1",
        )
        legacy_with_questions = AgreementType(
            id=catalog_type_id("legacy_nda"),
            key="mutual_nda",
            name="Mutual NDA",
            category="Confidentiality",
            status="active",
            version=1,
            schema={"questions": [{"key": "effective_date", "type": "date",
                                   "label": "Effective Date", "required": True}],
                    "clauses": []},
            template_key="mutual_nda_lk_v1",
        )
        db_session.add_all([legacy_dup, legacy_with_questions])
        await db_session.commit()

        await seed_catalog_agreement_types(db_session)
        await db_session.commit()

        dup = (
            await db_session.execute(
                select(AgreementType).where(AgreementType.key == "referral_agreement")
            )
        ).scalar_one()
        assert dup.status == "retired", "question-less duplicate must be retired"

        nda = (
            await db_session.execute(
                select(AgreementType).where(AgreementType.key == "mutual_nda")
            )
        ).scalar_one()
        assert nda.status == "active", "rows with questions are never touched"
        assert nda.schema["questions"][0]["key"] == "effective_date"

    async def test_active_types_all_have_questions_after_seed(self, db_session):
        """The invariant the wizard depends on: every type the /types listing
        serves (status=active) must resolve to a questionnaire."""
        legacy = AgreementType(
            id=catalog_type_id("legacy_vendor"),
            key="vendor_agreement",
            name="Vendor Agreement",
            category="Commercial",
            status="active",
            version=1,
            schema={"clauses": []},
            template_key="generic_agreement_lk_v1",
        )
        db_session.add(legacy)
        await db_session.commit()

        await seed_catalog_agreement_types(db_session)
        await db_session.commit()

        rows = (await db_session.execute(select(AgreementType))).scalars().all()
        active = [r for r in rows if r.status == "active"]
        assert active, "catalog must be active"
        for r in active:
            assert (r.schema or {}).get("questions"), (
                f"{r.key}: active without questions — wizard would 404"
            )

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
        assert loan["template_key"] == "financial_agreement_lk_v1"
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