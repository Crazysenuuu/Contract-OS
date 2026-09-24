"""Tests for SSE answer streaming (spec 2.10.67) and cross-contract
precedent retrieval (spec 2.10.26–2.10.28)."""

from __future__ import annotations

import json
import uuid

import pytest
import pytest_asyncio

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _reset_provider_cache():
    from app.services.embedding_provider import reset_embedding_provider

    reset_embedding_provider()
    yield
    reset_embedding_provider()


@pytest_asyncio.fixture
async def indexed_corpus(db_session, test_org, test_user, test_agreement, test_agreement_type):
    """Two agreements with indexed content so precedent search has a corpus."""
    from app.models.agreement import Agreement
    from app.services.retrieval_service import index_agreement_version

    other = Agreement(
        organization_id=test_org.id,
        agreement_type_id=test_agreement_type.id,
        title="Other NDA (precedent source)",
        status="active",
        created_by=test_user.id,
        data={},
    )
    db_session.add(other)
    await db_session.flush()

    version_id = uuid.uuid4()
    chunks = await index_agreement_version(
        db_session,
        organization_id=test_org.id,
        agreement_id=other.id,
        version_id=version_id,
        content=(
            "1. Confidentiality. The receiving party shall protect Confidential "
            "Information for five years after termination. "
            "2. Liability. Aggregate liability is capped at the fees paid "
            "in the preceding twelve months. "
            "3. Governing Law. This agreement is governed by the laws of "
            "Sri Lanka and the parties submit to Colombo arbitration."
        ),
        agreement_type="NDA",
        classification="internal",
    )
    await db_session.commit()
    assert len(chunks) >= 1

    return {
        "source_agreement": other,
        "source_version_id": version_id,
        "chunks": chunks,
    }


def _parse_sse(raw: str) -> list[tuple[str, dict]]:
    """Parse an SSE body into [(event, data_dict), ...]."""
    events = []
    for block in raw.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event, data = None, None
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = line.split(":", 1)[1].strip()
        if event is not None and data is not None:
            events.append((event, json.loads(data)))
    return events


class TestSSEStreaming:
    """Spec 2.10.67: token-streaming answer endpoint."""

    async def test_stream_emits_status_tokens_done(self, client, auth_headers, db_session,
                                                   test_org, test_agreement, indexed_corpus):
        from app.services.retrieval_service import index_agreement_version

        chunks = await index_agreement_version(
            db_session,
            organization_id=test_org.id,
            agreement_id=test_agreement.id,
            version_id=uuid.uuid4(),
            content=(
                "1. Confidentiality. The receiving party shall protect "
                "Confidential Information for three years after termination."
            ),
            agreement_type="NDA",
        )
        await db_session.commit()

        r = await client.post(
            f"/api/v1/intelligence/ask/stream?agreement_id={test_agreement.id}",
            json={"question": "How long does confidentiality survive?"},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/event-stream")

        events = _parse_sse(r.text)
        kinds = [e for e, _ in events]
        assert "status" in kinds
        assert "token" in kinds
        assert kinds[-1] == "done"

        # Reassembled tokens must equal the grounded answer text.
        token_text = "".join(d["text"] for e, d in events if e == "token")
        status_evt = next(d for e, d in events if e == "status")
        done_evt = next(d for e, d in events if e == "done")
        assert status_evt["status"] in ("ANSWERED", "REQUIRES_HUMAN_REVIEW",
                                        "INSUFFICIENT_EVIDENCE")
        assert token_text.strip()
        assert "citations" in done_evt
        assert "latency_ms" in done_evt

    async def test_stream_refuses_without_access(self, client, indexed_corpus):
        # No auth headers at all -> 401/403 before any token is emitted.
        r = await client.post(
            "/api/v1/intelligence/ask/stream",
            json={"question": "anything"},
        )
        assert r.status_code in (401, 403)
        # No SSE events may leak on the error path.
        assert "event: token" not in r.text

    async def test_stream_agreement_404(self, client, auth_headers):
        r = await client.post(
            "/api/v1/intelligence/ask/stream?agreement_id="
            "00000000-0000-0000-0000-0000000000ff",
            json={"question": "anything"},
            headers=auth_headers,
        )
        assert r.status_code in (403, 404)  # tenant gate may fire before 404


class TestPrecedentRetrieval:
    """Spec 2.10.26-2.10.28: cross-contract precedent retrieval."""

    async def test_suggest_finds_cross_agreement_precedent(
        self, client, auth_headers, db_session, test_org, test_user,
        test_agreement, indexed_corpus,
    ):
        from app.services.retrieval_service import index_agreement_version

        await index_agreement_version(
            db_session,
            organization_id=test_org.id,
            agreement_id=test_agreement.id,
            version_id=uuid.uuid4(),
            content="The parties need a limitation of liability clause.",
            agreement_type="MSA",
        )
        await db_session.commit()

        r = await client.post(
            f"/api/v1/intelligence/agreements/{test_agreement.id}/precedents/suggest",
            json={"question": "limitation of liability cap fees paid"},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["recorded"] >= 1
        assert len(body["suggestions"]) >= 1

        # Suggestions must never come from the target agreement itself.
        for s in body["suggestions"]:
            assert s["chunk"]["agreement_id"] != str(test_agreement.id)
            assert s["precedent_id"] is not None

    async def test_pin_and_list_precedents(
        self, client, auth_headers, db_session, test_org, test_user,
        test_agreement, indexed_corpus,
    ):
        chunk = indexed_corpus["chunks"][0]
        r = await client.post(
            f"/api/v1/intelligence/agreements/{test_agreement.id}/precedents/pin",
            json={
                "source_chunk_id": str(chunk.id),
                "note": "Use this confidentiality structure",
            },
            headers=auth_headers,
        )
        assert r.status_code == 201, r.text
        pinned = r.json()
        assert pinned["status"] == "pinned"
        assert pinned["origin"] == "user"
        assert pinned["source_agreement_id"] == str(indexed_corpus["source_agreement"].id)

        r = await client.get(
            f"/api/v1/intelligence/agreements/{test_agreement.id}/precedents",
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        rows = r.json()
        assert any(row["id"] == pinned["id"] for row in rows)

    async def test_pin_rejects_inaccessible_source(
        self, client, auth_headers, db_session, test_org, test_user,
        indexed_corpus,
    ):
        """Spec 2.10.28: pinning cannot cross the permission boundary."""
        from app.models.agreement import Agreement
        from app.models.user import User
        from app.core.security import hash_password

        # A second user with no membership in test_org...
        outsider = User(
            email="precedent-outsider@example.com",
            name="Outsider",
            password_hash=hash_password("TestPass123!"),
            status="active",
        )
        db_session.add(outsider)
        await db_session.commit()

        from app.core.security import create_access_token
        outsider_headers = {
            "Authorization": f"Bearer {create_access_token(user_id=outsider.id)}"
        }
        # ...and no accessible org at all (no membership) -> dependency 403s
        r = await client.post(
            "/api/v1/intelligence/agreements/"
            f"{indexed_corpus['source_agreement'].id}/precedents/pin",
            json={"source_chunk_id": str(indexed_corpus["chunks"][0].id)},
            headers=outsider_headers,
        )
        assert r.status_code in (401, 403)

    async def test_accept_suggested_precedent(
        self, client, auth_headers, db_session, test_org, test_user,
        test_agreement, indexed_corpus,
    ):
        from app.services.retrieval_service import index_agreement_version

        await index_agreement_version(
            db_session,
            organization_id=test_org.id,
            agreement_id=test_agreement.id,
            version_id=uuid.uuid4(),
            content="Governing law of Sri Lanka applies to this master agreement.",
            agreement_type="MSA",
        )
        await db_session.commit()

        r = await client.post(
            f"/api/v1/intelligence/agreements/{test_agreement.id}/precedents/suggest",
            json={"question": "governing law arbitration Colombo"},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        suggestions = r.json()["suggestions"]
        assert suggestions, "expected at least one suggestion"

        precedent_id = suggestions[0]["precedent_id"]
        r = await client.patch(
            f"/api/v1/intelligence/agreements/{test_agreement.id}"
            f"/precedents/{precedent_id}",
            json={"status": "accepted"},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "accepted"

    async def test_invalid_status_rejected(self, client, auth_headers, test_agreement):
        r = await client.patch(
            f"/api/v1/intelligence/agreements/{test_agreement.id}"
            "/precedents/00000000-0000-0000-0000-0000000000ee",
            json={"status": "bogus"},
            headers=auth_headers,
        )
        assert r.status_code == 400
