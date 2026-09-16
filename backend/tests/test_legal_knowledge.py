"""Tests for the legal knowledge engine (spec 1.10).

Covers source ingestion (never auto-activated), the human review gate,
source citation requirements, and safe rule evaluation against
agreements.
"""
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.legal_knowledge_service import (
    create_rule,
    ingest_source,
    set_rule_status,
    set_source_status,
    validate_agreement_legal_rules,
)


class TestLegalSources:
    async def test_ingest_starts_pending_review(self, db_session, test_org, test_user):
        source = await ingest_source(
            db_session,
            tenant_id=test_org.id,
            title="Employment (Amendment) Act",
            source_type="statute",
            jurisdiction_code="LK",
            content_text="Minimum notice period is 30 days.",
            created_by=test_user.id,
        )
        await db_session.commit()
        assert source.status == "pending_review"
        assert source.content_hash

    async def test_human_review_gate_activates(self, db_session, test_org, test_user):
        source = await ingest_source(
            db_session,
            tenant_id=test_org.id,
            title="PDPA",
            source_type="statute",
            jurisdiction_code="LK",
            content_text="Personal data processing requires consent.",
            created_by=test_user.id,
        )
        await db_session.flush()
        await set_source_status(
            db_session, source=source, status="active", reviewer=test_user.id
        )
        await db_session.commit()
        assert source.status == "active"
        assert source.versions[0].status == "active"


class TestLegalRules:
    async def test_rule_requires_source_citation(self, db_session, test_org, test_user):
        import pytest

        with pytest.raises(ValueError):
            await create_rule(
                db_session,
                tenant_id=test_org.id,
                rule_key="no_citation",
                title="Uncited rule",
                jurisdiction_code="LK",
                proposition="Uncited claim",
                source_id=None,
                source_version_id=None,
                created_by=test_user.id,
            )

    async def test_rule_human_review_gate(self, db_session, test_org, test_user):
        source = await ingest_source(
            db_session,
            tenant_id=test_org.id,
            title="Source",
            source_type="regulation",
            jurisdiction_code="LK",
            content_text="...",
            created_by=test_user.id,
        )
        await db_session.flush()
        version = source.versions[0]

        rule = await create_rule(
            db_session,
            tenant_id=test_org.id,
            rule_key="lk_currency",
            title="Agreements must be in LKR",
            jurisdiction_code="LK",
            proposition="Commercial agreements must state amounts in LKR.",
            source_id=source.id,
            source_version_id=version.id,
            executable_condition={"op": "eq", "field": "data.currency", "value": "LKR"},
            severity="blocking",
            created_by=test_user.id,
        )
        await db_session.flush()
        assert rule.status == "pending_review"

        await set_rule_status(
            db_session, rule=rule, status="active", reviewer=test_user.id
        )
        await db_session.commit()
        assert rule.status == "active"


class TestRuleEvaluation:
    async def test_validation_flags_violation(self, db_session, test_org, test_user, test_agreement):
        # Active blocking rule: currency must be LKR.
        source = await ingest_source(
            db_session,
            tenant_id=test_org.id,
            title="Central Bank Directive",
            source_type="official_guidance",
            jurisdiction_code="LK",
            content_text="...",
            created_by=test_user.id,
        )
        await db_session.flush()
        version = source.versions[0]
        rule = await create_rule(
            db_session,
            tenant_id=test_org.id,
            rule_key="lk_currency",
            title="LKR currency",
            jurisdiction_code="LK",
            proposition="Amounts must be in LKR.",
            source_id=source.id,
            source_version_id=version.id,
            executable_condition={"op": "eq", "field": "data.currency", "value": "LKR"},
            severity="blocking",
            created_by=test_user.id,
        )
        await set_rule_status(
            db_session, rule=rule, status="active", reviewer=test_user.id
        )

        # Agreement in USD → violation.
        test_agreement.governing_law = "LK"
        test_agreement.data = {"currency": "USD"}
        await db_session.flush()

        result = await validate_agreement_legal_rules(db_session, test_agreement)
        assert result["can_proceed"] is False
        assert result["blocking_count"] == 1
        assert result["findings"][0]["rule_key"] == "lk_currency"
        assert result["findings"][0]["source"]["jurisdiction_code"] == "LK"

    async def test_validation_passes_when_condition_met(self, db_session, test_org, test_user, test_agreement):
        source = await ingest_source(
            db_session,
            tenant_id=test_org.id,
            title="Directive",
            source_type="official_guidance",
            jurisdiction_code="LK",
            content_text="...",
            created_by=test_user.id,
        )
        await db_session.flush()
        version = source.versions[0]
        rule = await create_rule(
            db_session,
            tenant_id=test_org.id,
            rule_key="lk_currency",
            title="LKR currency",
            jurisdiction_code="LK",
            proposition="Amounts must be in LKR.",
            source_id=source.id,
            source_version_id=version.id,
            executable_condition={"op": "eq", "field": "data.currency", "value": "LKR"},
            severity="blocking",
            created_by=test_user.id,
        )
        await set_rule_status(
            db_session, rule=rule, status="active", reviewer=test_user.id
        )

        test_agreement.governing_law = "LK"
        test_agreement.data = {"currency": "LKR"}
        await db_session.flush()

        result = await validate_agreement_legal_rules(db_session, test_agreement)
        assert result["can_proceed"] is True
        assert result["blocking_count"] == 0

    async def test_malformed_condition_surfaces_finding(self, db_session, test_org, test_user, test_agreement):
        source = await ingest_source(
            db_session,
            tenant_id=test_org.id,
            title="Source",
            source_type="regulation",
            jurisdiction_code="LK",
            content_text="...",
            created_by=test_user.id,
        )
        await db_session.flush()
        version = source.versions[0]
        rule = await create_rule(
            db_session,
            tenant_id=test_org.id,
            rule_key="bad_op",
            title="Bad operator",
            jurisdiction_code="LK",
            proposition="Test",
            source_id=source.id,
            source_version_id=version.id,
            executable_condition={"op": "teleport", "field": "data.currency", "value": "LKR"},
            severity="blocking",
            created_by=test_user.id,
        )
        await set_rule_status(
            db_session, rule=rule, status="active", reviewer=test_user.id
        )
        test_agreement.governing_law = "LK"
        await db_session.flush()

        result = await validate_agreement_legal_rules(db_session, test_agreement)
        assert result["blocking_count"] == 1
        assert "malformed" in result["findings"][0]["message"]


class TestLegalApi:
    async def test_source_and_rule_api_flow(self, client, test_org, auth_headers, db_session, test_user):
        # Promote user to admin so the review-gate endpoints work.
        test_user.is_admin = True
        await db_session.commit()

        create = await client.post(
            "/api/v1/legal/sources",
            headers=auth_headers,
            json={
                "title": "Data Protection Act",
                "source_type": "statute",
                "jurisdiction_code": "LK",
                "content_text": "Consent required for personal data.",
            },
        )
        assert create.status_code == 201
        source = create.json()
        assert source["status"] == "pending_review"
        source_id = source["id"]
        version_id = source["versions"][0]["id"]

        approved = await client.post(
            f"/api/v1/legal/sources/{source_id}/approve",
            headers=auth_headers,
            json={"notes": "Reviewed"},
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "active"

        rule = await client.post(
            "/api/v1/legal/rules",
            headers=auth_headers,
            json={
                "rule_key": "lk_consent",
                "title": "Consent required",
                "jurisdiction_code": "LK",
                "proposition": "Processing personal data requires consent.",
                "source_id": source_id,
                "source_version_id": version_id,
                "executable_condition": {"op": "exists", "field": "data.consent"},
                "severity": "blocking",
            },
        )
        assert rule.status_code == 201
        assert rule.json()["status"] == "pending_review"

        approved_rule = await client.post(
            f"/api/v1/legal/rules/{rule.json()['id']}/approve",
            headers=auth_headers,
            json={"notes": "Reviewed"},
        )
        assert approved_rule.status_code == 200
        assert approved_rule.json()["status"] == "active"

    async def test_uncited_rule_rejected_by_api(self, client, test_org, auth_headers):
        # source_id is required by the schema, so an uncited rule is
        # rejected at validation time (422) before it can be created.
        response = await client.post(
            "/api/v1/legal/rules",
            headers=auth_headers,
            json={
                "rule_key": "uncited",
                "title": "No source",
                "jurisdiction_code": "LK",
                "proposition": "No citation.",
                "source_id": None,
                "source_version_id": None,
            },
        )
        assert response.status_code in (400, 422)

    async def test_agreement_legal_validation_api(
        self, client, test_org, test_agreement, auth_headers, db_session, test_user
    ):
        test_user.is_admin = True
        await db_session.commit()

        source = await ingest_source(
            db_session,
            tenant_id=test_org.id,
            title="Directive",
            source_type="official_guidance",
            jurisdiction_code="LK",
            content_text="...",
            created_by=test_user.id,
        )
        await db_session.flush()
        version = source.versions[0]
        rule = await create_rule(
            db_session,
            tenant_id=test_org.id,
            rule_key="lk_currency",
            title="LKR",
            jurisdiction_code="LK",
            proposition="LKR required.",
            source_id=source.id,
            source_version_id=version.id,
            executable_condition={"op": "eq", "field": "data.currency", "value": "LKR"},
            severity="blocking",
            created_by=test_user.id,
        )
        await set_rule_status(db_session, rule=rule, status="active", reviewer=test_user.id)
        test_agreement.governing_law = "LK"
        test_agreement.data = {"currency": "USD"}
        await db_session.commit()

        response = await client.get(
            f"/api/v1/agreements/{test_agreement.id}/legal-validation",
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["can_proceed"] is False
        assert response.json()["blocking_count"] == 1