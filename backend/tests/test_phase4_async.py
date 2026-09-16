"""Integration tests for async Phase 4 bulk/tenant/performance/clause endpoints (F1).

Verifies the AsyncSession-backed service conversions work end-to-end over the
HTTP API.
"""
import pytest
from sqlalchemy import select

from app.models.agreement import Agreement


class TestBulkOperations:
    async def test_bulk_import_creates_agreements(
        self, client, auth_headers, db_session, test_org, test_agreement_type
    ):
        csv_content = "\n".join([
            "title,status,agreement_type",
            "Imported NDA One,draft,mutual_nda",
            "Imported NDA Two,sent,mutual_nda",
        ])
        resp = await client.post(
            "/api/v1/phase4/bulk/import",
            headers=auth_headers,
            json={"csv_content": csv_content, "import_type": "agreements"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["total_items"] == 2

        job_id = resp.json()["job_id"]
        detail = await client.get(
            f"/api/v1/phase4/bulk/jobs/{job_id}", headers=auth_headers
        )
        assert detail.status_code == 200
        body = detail.json()
        assert body["successful_items"] == 2, body.get("errors")

        result = await db_session.execute(
            select(Agreement).where(Agreement.organization_id == test_org.id)
        )
        titles = {a.title for a in result.scalars().all()}
        assert {"Imported NDA One", "Imported NDA Two"}.issubset(titles)

    async def test_bulk_status_change(
        self, client, auth_headers, db_session, test_org, test_agreement
    ):
        resp = await client.post(
            "/api/v1/phase4/bulk/action",
            headers=auth_headers,
            json={
                "action": "status_change",
                "filter_criteria": {"status": "draft"},
                "options": {"new_status": "sent"},
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] in ("completed", "partial")
        assert resp.json()["total_items"] >= 1

        await db_session.refresh(test_agreement)
        assert test_agreement.status == "sent"

    async def test_bulk_export_and_download(
        self, client, auth_headers, test_agreement
    ):
        resp = await client.post(
            "/api/v1/phase4/bulk/export?export_type=agreements",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        job_id = resp.json()["job_id"]

        dl = await client.get(
            f"/api/v1/phase4/bulk/export/{job_id}/download", headers=auth_headers
        )
        assert dl.status_code == 200
        assert "Test NDA Agreement" in dl.text

    async def test_saved_filters_crud(
        self, client, auth_headers, db_session, test_user, test_org
    ):
        create = await client.post(
            "/api/v1/phase4/filters",
            headers=auth_headers,
            json={
                "name": "Executed 2026",
                "entity_type": "agreement",
                "filters": {"status": "executed"},
                "is_shared": True,
            },
        )
        assert create.status_code == 200, create.text
        filter_id = create.json()["id"]

        listing = await client.get("/api/v1/phase4/filters", headers=auth_headers)
        assert listing.status_code == 200
        assert any(f["id"] == filter_id for f in listing.json())

        delete = await client.delete(
            f"/api/v1/phase4/filters/{filter_id}", headers=auth_headers
        )
        assert delete.status_code == 200


class TestTenantEndpoints:
    async def test_tenant_lifecycle_and_branding(
        self, client, auth_headers, test_org
    ):
        create = await client.post(
            "/api/v1/phase4/tenant?slug=test-corp-pro&plan=professional",
            headers=auth_headers,
        )
        assert create.status_code == 200, create.text
        assert create.json()["slug"] == "test-corp-pro"

        fetched = await client.get("/api/v1/phase4/tenant", headers=auth_headers)
        assert fetched.status_code == 200
        assert fetched.json()["exists"] is True
        assert fetched.json()["plan"] == "professional"

        patch = await client.patch(
            "/api/v1/phase4/tenant/branding",
            headers=auth_headers,
            json={"company_name": "Test Corp Pro", "primary_color": "#123456"},
        )
        assert patch.status_code == 200
        assert patch.json()["company_name"] == "Test Corp Pro"

        css = await client.get("/api/v1/phase4/tenant/css", headers=auth_headers)
        assert css.status_code == 200
        assert "#123456" in css.text

        themes = await client.get("/api/v1/phase4/tenant/themes", headers=auth_headers)
        assert themes.status_code == 200

        limits = await client.get("/api/v1/phase4/tenant/limits", headers=auth_headers)
        assert limits.status_code == 200
        assert limits.json()["allowed"] is True

        audit = await client.get("/api/v1/phase4/tenant/audit", headers=auth_headers)
        assert audit.status_code == 200
        assert any(l["action"] == "branding_updated" for l in audit.json())


class TestPerformanceEndpoints:
    async def test_optimized_agreements(
        self, client, auth_headers, test_agreement
    ):
        resp = await client.get(
            "/api/v1/phase4/performance/agreements?page=1&page_size=10",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["pagination"]["total"] >= 1
        assert any(i["id"] == str(test_agreement.id) for i in body["items"])

    async def test_background_job_queue(self, client, auth_headers, test_agreement):
        enq = await client.post(
            "/api/v1/phase4/jobs/enqueue",
            headers=auth_headers,
            json={
                "job_type": "notification",
                "payload": {
                    "agreement_id": str(test_agreement.id),
                    "organization_id": str(test_agreement.organization_id),
                    "agreement_title": test_agreement.title,
                    "previous_state": "draft",
                    "current_state": "sent",
                    "actor_name": "System",
                },
            },
        )
        assert enq.status_code == 200, enq.text
        job_id = enq.json()["job_id"]

        processed = await client.post(
            "/api/v1/phase4/jobs/process?max_jobs=10", headers=auth_headers
        )
        assert processed.status_code == 200
        assert any(j["id"] == job_id for j in processed.json().get("jobs", []))

        status = await client.get(
            f"/api/v1/phase4/jobs/{job_id}", headers=auth_headers
        )
        assert status.status_code == 200
        assert status.json()["status"] in ("processing", "completed", "failed")


class TestDocumentIntelligence:
    async def test_clause_extract_to_library(
        self, client, auth_headers, test_agreement
    ):
        text = (
            "1. CONFIDENTIALITY\n"
            "Each party shall keep all Confidential Information confidential and "
            "shall not disclose it to any third party.\n"
            "2. LIMITATION OF LIABILITY\n"
            "Neither party shall be liable for indirect or consequential damages. "
            "Aggregate liability is limited to fees paid.\n"
            "3. TERMINATION\n"
            "Either party may terminate this Agreement upon 30 days notice."
        )
        extracted = await client.post(
            "/api/v1/phase4/clauses/extract",
            headers=auth_headers,
            json={"agreement_id": str(test_agreement.id), "text": text},
        )
        assert extracted.status_code == 200, extracted.text
        clauses = extracted.json()["clauses"]
        assert len(clauses) >= 2
        clause_id = clauses[0]["id"]

        listed = await client.get(
            f"/api/v1/phase4/clauses?agreement_id={test_agreement.id}",
            headers=auth_headers,
        )
        assert listed.status_code == 200
        assert len(listed.json()) >= 2

        lib = await client.post(
            "/api/v1/phase4/clauses/add-to-library",
            headers=auth_headers,
            json={
                "clause_id": clause_id,
                "title": "Standard Confidentiality",
                "jurisdictions": ["LK"],
                "agreement_types": ["mutual_nda"],
            },
        )
        assert lib.status_code == 200, lib.text
        assert lib.json()["id"]

        stats = await client.get("/api/v1/phase4/clauses/stats", headers=auth_headers)
        assert stats.status_code == 200
        assert stats.json()["total_clauses_extracted"] >= 2
        assert stats.json()["library_entries"] >= 1

    async def test_smart_tag_crud(self, client, auth_headers, test_org):
        create = await client.post(
            "/api/v1/phase4/tags?name=Urgent&color=%23ff0000&description=Needs+attention",
            headers=auth_headers,
        )
        assert create.status_code == 200, create.text

        listing = await client.get("/api/v1/phase4/tags", headers=auth_headers)
        assert listing.status_code == 200
        assert any(t["name"] == "Urgent" for t in listing.json())