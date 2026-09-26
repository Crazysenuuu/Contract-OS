"""Tests for ingestion intelligence (spec §3.21.22-46, §3.21.85-96)."""

import uuid

import pytest
from sqlalchemy import select

from app.services.ingestion_intelligence import (
    CandidateError,
    create_candidate,
    detect_conflicts,
    promote_candidate,
    provenance_chain,
    record_provenance,
    resolve_conflict,
    verify_candidate,
)


async def _make_document(db, org, user):
    from app.models.document import Document, DocumentType

    doc_type = DocumentType(
        code=f"ingest_{uuid.uuid4().hex[:6]}", name="Ingested", configuration={}
    )
    db.add(doc_type)
    await db.flush()
    document = Document(
        organization_id=org.id,
        created_by=user.id,
        document_type_id=doc_type.id,
        filename="ingested.pdf",
        size_bytes=1024,
        media_type="application/pdf",
        title="Ingested document",
        storage_key=f"ingest/{uuid.uuid4().hex}/ingested.pdf",
        sha256="0" * 64,
    )
    db.add(document)
    await db.flush()
    return document


class TestCandidates:
    async def test_candidate_starts_pending(self, db_session, test_org, test_user):
        document = await _make_document(db_session, test_org, test_user)
        candidate = await create_candidate(
            db_session,
            organization_id=test_org.id,
            document_id=document.id,
            candidate_type="lifecycle_term",
            field_key="effective_date",
            value={"date": "2026-01-01"},
            source_ref={"page": 1, "quote": "effective as of January 1, 2026"},
            confidence=0.93,
        )
        assert candidate.status == "pending"
        assert candidate.source_ref["page"] == 1

    async def test_verify_then_promote(self, db_session, test_org, test_user):
        document = await _make_document(db_session, test_org, test_user)
        candidate = await create_candidate(
            db_session,
            organization_id=test_org.id,
            document_id=document.id,
            candidate_type="metadata",
            field_key="governing_law",
            value_text="Sri Lanka",
            confidence=0.8,
        )
        await verify_candidate(
            db_session, candidate_id=candidate.id, verified_by=test_user.id, accept=True
        )
        promoted = await promote_candidate(
            db_session,
            candidate_id=candidate.id,
            promoted_by=test_user.id,
            target="agreement.data.governing_law",
            target_id=uuid.uuid4(),
        )
        assert promoted.status == "promoted"
        assert promoted.promoted_target == "agreement.data.governing_law"

    async def test_cannot_promote_unverified(self, db_session, test_org, test_user):
        document = await _make_document(db_session, test_org, test_user)
        candidate = await create_candidate(
            db_session,
            organization_id=test_org.id,
            document_id=document.id,
            candidate_type="metadata",
            field_key="currency",
            value_text="USD",
            confidence=0.5,
        )
        with pytest.raises(CandidateError, match="verified"):
            await promote_candidate(
                db_session,
                candidate_id=candidate.id,
                promoted_by=test_user.id,
                target="agreement.data.currency",
                target_id=uuid.uuid4(),
            )

    async def test_verification_idempotent(self, db_session, test_org, test_user):
        document = await _make_document(db_session, test_org, test_user)
        candidate = await create_candidate(
            db_session,
            organization_id=test_org.id,
            document_id=document.id,
            candidate_type="metadata",
            field_key="contract_value",
            value_text="1000",
            confidence=0.7,
        )
        await verify_candidate(
            db_session, candidate_id=candidate.id, verified_by=test_user.id, accept=False
        )
        again = await verify_candidate(
            db_session, candidate_id=candidate.id, verified_by=test_user.id, accept=True
        )
        # Second verification does not flip the decided candidate.
        assert again.status == "rejected"


class TestConflicts:
    async def test_conflicting_values_flagged(self, db_session, test_org, test_user):
        document = await _make_document(db_session, test_org, test_user)
        await create_candidate(
            db_session,
            organization_id=test_org.id,
            document_id=document.id,
            candidate_type="lifecycle_term",
            field_key="termination_notice_days",
            value={"days": 30},
            confidence=0.9,
        )
        conflict = await create_candidate(
            db_session,
            organization_id=test_org.id,
            document_id=document.id,
            candidate_type="lifecycle_term",
            field_key="termination_notice_days",
            value={"days": 60},
            confidence=0.85,
        )
        # create_candidate ran detection; find the open conflict.
        conflicts = (
            await db_session.execute(
                select(__import__("app.models.ingestion_intelligence", fromlist=["ExtractionConflict"]).ExtractionConflict).where(
                    __import__("app.models.ingestion_intelligence", fromlist=["ExtractionConflict"]).ExtractionConflict.document_id
                    == document.id
                )
            )
        ).scalars().all()
        assert len(conflicts) == 1
        assert conflicts[0].status == "open"

        # Agreeing value does not create a second conflict.
        await create_candidate(
            db_session,
            organization_id=test_org.id,
            document_id=document.id,
            candidate_type="lifecycle_term",
            field_key="governing_law",
            value_text="Singapore",
            confidence=0.99,
        )
        assert conflicts[0].field_key == "termination_notice_days"

        # Resolve: winner verified, loser rejected.
        candidates = (
            await db_session.execute(
                select(__import__("app.models.ingestion_intelligence", fromlist=["ExtractionCandidate"]).ExtractionCandidate).where(
                    __import__("app.models.ingestion_intelligence", fromlist=["ExtractionCandidate"]).ExtractionCandidate.field_key
                    == "termination_notice_days"
                )
            )
        ).scalars().all()
        winner = candidates[0]
        await resolve_conflict(
            db_session,
            conflict_id=conflicts[0].id,
            winner_candidate_id=winner.id,
            resolved_by=test_user.id,
        )
        assert conflicts[0].status == "resolved"

    async def test_agreeing_values_no_conflict(self, db_session, test_org, test_user):
        from app.models.ingestion_intelligence import ExtractionConflict

        document = await _make_document(db_session, test_org, test_user)
        await create_candidate(
            db_session,
            organization_id=test_org.id,
            document_id=document.id,
            candidate_type="metadata",
            field_key="currency",
            value_text="USD",
            confidence=0.9,
        )
        await create_candidate(
            db_session,
            organization_id=test_org.id,
            document_id=document.id,
            candidate_type="metadata",
            field_key="currency",
            value_text="USD",
            confidence=0.88,
        )
        conflicts = (
            await db_session.execute(
                select(ExtractionConflict).where(
                    ExtractionConflict.document_id == document.id
                )
            )
        ).scalars().all()
        assert conflicts == []


class TestProvenance:
    async def test_chain_records_stages_with_hashes(self, db_session, test_org, test_user):
        document = await _make_document(db_session, test_org, test_user)
        await record_provenance(
            db_session,
            organization_id=test_org.id,
            document_id=document.id,
            stage="ocr",
            input_payload={"bytes": 12345},
            output_payload={"text": "CONTRACT..."},
            parser_version="tesseract-5",
        )
        await record_provenance(
            db_session,
            organization_id=test_org.id,
            document_id=document.id,
            stage="extract",
            input_payload={"text": "CONTRACT..."},
            output_payload={"candidates": 3},
            parser_version="hybrid-2",
        )
        chain = await provenance_chain(db_session, document_id=document.id)
        assert [c["stage"] for c in chain] == ["ocr", "extract"]
        assert all(c["input_hash"] and c["output_hash"] for c in chain)
        # Hashes are content-addressed, not random.
        assert chain[0]["output_hash"] == chain[1]["input_hash"]
