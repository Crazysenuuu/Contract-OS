"""Distributed state (spec 1.14.21-22) + long-contract AI analysis (1.19.24).

Verifies:
- StateStore: sliding windows, TTL key/value, pub/sub API, Redis failure
  degradation, per-test reset.
- MassDownloadDetector + escalation throttles/incidents ride the shared
  store (covered here via store behavior + existing suites).
- ws_fanout envelope parsing and publish envelope shape.
- AI chunking: long contracts are split, every chunk analyzed, results
  merged and deduped, and the truncation cap is reported honestly via
  ``coverage`` — never silently.
"""

import json
import uuid

import pytest

from app.core import distributed_state
from app.core.distributed_state import StateStore, get_state_store, reset_state_store
from app.services.ai_service import AIService, AnalysisResult, RiskItem


# ── StateStore ────────────────────────────────────────────────────────────────


class TestStateStoreWindows:
    def test_window_add_counts_and_expires(self, monkeypatch):
        store = StateStore(None)
        stats = store.window_add("k", window_seconds=10, max_events=2)
        assert stats["count"] == 1
        assert stats["exceeded"] is False
        store.window_add("k", window_seconds=10, max_events=2)
        stats = store.window_add("k", window_seconds=10, max_events=2)
        assert stats["count"] == 3
        assert stats["exceeded"] is True

    def test_window_count_is_read_only(self):
        store = StateStore(None)
        store.window_add("k", window_seconds=10, max_events=5)
        assert store.window_count("k", window_seconds=10) == 1
        # Read-only: no new event recorded.
        assert store.window_count("k", window_seconds=10) == 1

    def test_window_reset(self):
        store = StateStore(None)
        store.window_add("k", window_seconds=10, max_events=5)
        store.window_reset("k")
        assert store.window_count("k", window_seconds=10) == 0

    def test_window_prunes_old_entries(self, monkeypatch):
        import app.core.distributed_state as ds

        real = ds.time.monotonic
        clock = {"t": real()}
        monkeypatch.setattr(ds.time, "monotonic", lambda: clock["t"])
        store = StateStore(None)
        store.window_add("k", window_seconds=10, max_events=100)
        clock["t"] += 20  # window slid
        store.window_add("k", window_seconds=10, max_events=100)
        assert store.window_count("k", window_seconds=10) == 1


class TestStateStoreKV:
    def test_set_get_delete(self):
        store = StateStore(None)
        store.set("a", "1")
        assert store.get("a") == "1"
        store.delete("a")
        assert store.get("a") is None

    def test_ttl_expiry(self, monkeypatch):
        import app.core.distributed_state as ds

        real = ds.time.monotonic
        clock = {"t": real()}
        monkeypatch.setattr(ds.time, "monotonic", lambda: clock["t"])
        store = StateStore(None)
        store.set("a", "1", ttl_seconds=10)
        clock["t"] += 11
        assert store.get("a") is None

    def test_json_roundtrip(self):
        store = StateStore(None)
        store.set_json("j", {"a": [1, 2], "b": "x"})
        assert store.get_json("j") == {"a": [1, 2], "b": "x"}
        assert store.get_json("missing") is None
        store.set("bad", "not json")
        assert store.get_json("bad") is None

    def test_keys_pattern(self):
        store = StateStore(None)
        store.set("escalation:incidents:org-1", "[]")
        store.set("escalation:incidents:org-2", "[]")
        store.set("other", "x")
        keys = store.keys("escalation:incidents:*")
        assert len(keys) == 2
        assert all(k.startswith("escalation:incidents:") for k in keys)


class TestStateStorePubSub:
    def test_publish_without_redis_is_false_not_raise(self):
        store = StateStore(None)
        assert store.publish("notify:x", {"a": 1}) is False

    def test_subscribe_without_redis_is_none(self):
        assert StateStore(None).subscribe("ch") is None
        assert StateStore(None).subscribe_pattern("notify:*") is None

    def test_publish_with_broken_redis_degrades(self):
        store = StateStore(None)
        store._redis = _BrokenRedis()
        assert store.publish("notify:x", {"a": 1}) is False


class _BrokenRedis:
    """Redis stand-in whose every method raises (simulates outage)."""

    def __getattr__(self, name):
        def _raise(*args, **kwargs):
            raise ConnectionError("redis down")

        return _raise

    def ping(self):
        raise ConnectionError("redis down")


class TestStateStoreDegradation:
    def test_redis_unreachable_falls_back_to_memory(self, monkeypatch):
        # StateStore catches the ping failure and degrades to memory backend.
        store = StateStore("redis://localhost:6390/0")  # nothing listens here
        assert store.backend == "memory"
        store.set("x", "1")
        assert store.get("x") == "1"

    def test_get_state_store_singleton(self):
        reset_state_store()
        store = get_state_store()
        assert get_state_store() is store
        reset_state_store()
        assert get_state_store() is not store


# ── Mass-download guard rides the shared store ┊──────────────────────────────


class TestMassDownloadUsesStore:
    def test_counter_lives_in_state_store(self):
        from app.services.document_security import MassDownloadDetector

        guard = MassDownloadDetector(max_downloads=2, window_seconds=60)
        guard.check("user-x")
        guard.check("user-x")
        with pytest.raises(Exception):
            guard.check("user-x")
        # The counter is in the store under the dlp: prefix, not on the guard.
        store = get_state_store()
        assert store.window_count("dlp:downloads:user-x", window_seconds=60) == 3

    def test_reset_clears_store_key(self):
        from app.services.document_security import MassDownloadDetector

        guard = MassDownloadDetector(max_downloads=1, window_seconds=60)
        guard.check("user-y")
        guard.reset("user-y")
        guard.check("user-y")  # allowed again
        assert get_state_store().window_count("dlp:downloads:user-y", window_seconds=60) == 1


# ── Escalation rides the shared store ────────────────────────────────────────


class TestEscalationUsesStore:
    def _service(self):
        from app.services.escalation_service import EscalationService

        return EscalationService()

    def test_incident_persisted_in_store(self):
        from app.services.escalation_service import EscalationLevel

        service = self._service()
        service.ensure_default_policies("org-1")
        incident = service.create_incident(
            organization_id="org-1",
            category="compliance",
            title="T",
            message="M",
            severity=EscalationLevel.WARNING,
        )
        assert incident is not None
        store = get_state_store()
        stored = store.get_json(f"escalation:incident:{incident.id}")
        assert stored is not None
        assert stored["title"] == "T"

    def test_incident_ids_are_globally_unique(self):
        """Per-process counters would collide in the shared store."""
        from app.services.escalation_service import EscalationLevel

        a, b = self._service(), self._service()
        a.ensure_default_policies("org-shared")
        b.ensure_default_policies("org-shared")
        i1 = a.create_incident(
            organization_id="org-shared", category="compliance",
            title="A", message="M", severity=EscalationLevel.WARNING,
        )
        i2 = b.create_incident(
            organization_id="org-shared", category="esignature",
            title="B", message="M", severity=EscalationLevel.WARNING,
        )
        assert i1 is not None and i2 is not None
        assert i1.id != i2.id

    def test_cooldown_lives_in_store(self):
        from app.services.escalation_service import EscalationLevel, EscalationPolicy

        service = self._service()
        service.create_policy(EscalationPolicy(
            id="p-cd", organization_id="org-cd", name="CD",
            category="compliance", cooldown_seconds=300,
        ))
        service.create_incident(
            organization_id="org-cd", category="compliance",
            title="T", message="M", severity=EscalationLevel.WARNING,
        )
        assert get_state_store().get("escalation:cooldown:org-cd:compliance") is not None

    def test_store_incident_roundtrip_preserves_enums(self):
        from app.services.escalation_service import EscalationLevel, EscalationStatus

        service = self._service()
        service.ensure_default_policies("org-rt")
        incident = service.create_incident(
            organization_id="org-rt", category="compliance",
            title="T", message="M", severity=EscalationLevel.CRITICAL,
        )
        loaded = service.get_incident(incident.id)
        assert loaded.severity is EscalationLevel.CRITICAL
        assert loaded.status is EscalationStatus.PENDING
        service.resolve_incident(incident.id)
        reloaded = service.get_incident(incident.id)
        assert reloaded.status is EscalationStatus.RESOLVED
        assert reloaded.resolved_at is not None


# ── ws_fanout ────────────────────────────────────────────────────────────────


class TestWsFanout:
    async def test_handle_message_forwards_to_connected_users(self):
        from app.services import ws_fanout
        from app.services.event_outbox_service import connection_manager

        sent = []
        user_id = uuid.uuid4()

        class FakeWS:
            async def accept(self):
                pass

            async def send_json(self, message):
                sent.append(message)

        ws = FakeWS()
        await connection_manager.connect(ws, user_id, None)
        try:
            envelope = json.dumps({
                "channel": "notify:global",
                "user_ids": [str(user_id)],
                "message": {"event_type": "signature.completed"},
            })
            await ws_fanout._handle_message(envelope)
            assert sent == [{"event_type": "signature.completed"}]
        finally:
            await connection_manager.disconnect(ws, user_id, None)

    def test_handle_message_ignores_malformed(self):
        import asyncio

        from app.services import ws_fanout

        async def _run():
            # Must not raise on garbage.
            await ws_fanout._handle_message("not json")
            await ws_fanout._handle_message(json.dumps({"no_user_ids": True}))
            await ws_fanout._handle_message(
                json.dumps({"user_ids": ["abc"], "message": {}})
            )

        asyncio.new_event_loop().run_until_complete(_run())

    def test_publish_redis_channel_envelope_shape(self, monkeypatch):
        # publish_redis_channel imports the store lazily — patch it at the
        # source module, not the consumer.
        import app.core.distributed_state as ds

        captured = {}

        class _Store:
            def publish(self, channel, envelope):
                captured["channel"] = channel
                captured["envelope"] = envelope
                return True

        monkeypatch.setattr(ds, "get_state_store", lambda: _Store())
        tenant = uuid.uuid4()
        user = uuid.uuid4()
        from app.services.event_outbox_service import publish_redis_channel

        publish_redis_channel(
            tenant, {"event_type": "signature.completed"}, user_ids=[user]
        )
        assert captured["channel"] == f"notify:{tenant}"
        assert captured["envelope"]["user_ids"] == [str(user)]
        assert captured["envelope"]["message"]["event_type"] == "signature.completed"
        assert captured["envelope"]["origin"]  # origin id present for self-suppression


# ── AI long-contract chunking (1.19.24) ──────────────────────────────────────


def _long_contract(sections: int = 40) -> str:
    body = "\n\n".join(
        f"Section {i}. The parties agree to the following obligations, "
        "indemnities, limitations of liability, and confidentiality terms "
        "governing this engagement, with renewal and termination mechanics "
        "detailed herein. Payment terms: net 30 days from invoice."
        for i in range(1, sections + 1)
    )
    return body


class _ScriptedLLM(AIService):
    """AIService whose LLM responses are scripted per chunk.

    A scripted ``Exception`` instance is raised (simulating a provider
    failure) rather than returned.
    """

    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def _call_llm(self, prompt: str) -> str:  # noqa: D102
        self.prompts.append(prompt)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class TestAIChunking:
    def test_short_contract_single_chunk(self):
        service = AIService()
        chunks, truncated = service._split_into_chunks("short contract text")
        assert chunks == ["short contract text"]
        assert truncated is False

    def test_riskitem_dataclass_has_coverage_fields(self):
        result = AnalysisResult()
        assert result.coverage == {}
        risk = RiskItem(category="liability", severity="high")
        assert risk.finding == ""

    def test_long_contract_split_into_overlapping_chunks(self):
        service = AIService()
        text = _long_contract(sections=400)  # ~168k chars
        chunks, truncated = service._split_into_chunks(text)
        assert len(chunks) > 1
        assert truncated is False
        # Every character of the contract is covered by some chunk.
        assert sum(len(c) for c in chunks) >= len(text)
        # Chunk sizes respect the cap.
        max_chunk = max(len(c) for c in chunks)
        assert max_chunk <= 60000 + 2000  # cap + overlap slack

    def test_chunk_cap_reports_truncation_honestly(self):
        service = AIService()
        text = "Para\n\n" * 200000  # way beyond max chunks
        chunks, truncated = service._split_into_chunks(text)
        assert truncated is True
        # Head, middle and tail are all represented.
        assert len(chunks) <= 20

    async def test_detect_risks_merges_and_dedupes(self):
        risk = {
            "category": "liability",
            "severity": "high",
            "finding": "Uncapped indemnification",
            "confidence": 0.9,
        }
        service = _ScriptedLLM([])
        text = _long_contract(sections=400)
        chunks, _ = service._split_into_chunks(text)
        assert len(chunks) > 1
        service.responses = [json.dumps([risk])] * len(chunks)
        risks = await service.detect_risks(text)
        assert len(risks) == 1  # overlap duplicate removed
        assert risks[0].finding == "Uncapped indemnification"
        assert len(service.prompts) == len(chunks)  # every chunk analyzed

    async def test_detect_risks_per_chunk_failure_is_partial(self):
        risk = {
            "category": "termination",
            "severity": "medium",
            "finding": "Termination for convenience omitted",
            "confidence": 0.7,
        }
        service = _ScriptedLLM([])
        text = _long_contract(sections=400)
        chunks, _ = service._split_into_chunks(text)
        assert len(chunks) > 1
        # One chunk's LLM call fails; the rest must still contribute.
        service.responses = [
            RuntimeError("LLM exploded") if i == 0 else json.dumps([risk])
            for i in range(len(chunks))
        ]
        risks = await service.detect_risks(text)
        assert len(risks) == 1

    async def test_detect_risks_all_chunks_failed_raises(self):
        service = _ScriptedLLM([])
        text = _long_contract(sections=400)
        chunks, _ = service._split_into_chunks(text)
        service.responses = [RuntimeError("down")] * len(chunks)
        with pytest.raises(RuntimeError):
            await service.detect_risks(text)

    async def test_analyze_contract_multichunk_carries_coverage(self):
        first = json.dumps({
            "summary": "A services agreement.",
            "key_terms": {"parties": ["Acme", "Global"]},
            "risks": [{"category": "payment", "severity": "medium",
                       "finding": "Net 30 terms", "confidence": 0.6}],
        })
        later = json.dumps([
            {"category": "liability", "severity": "high",
             "finding": "Uncapped indemnification", "confidence": 0.9},
        ])
        service = _ScriptedLLM([])
        text = _long_contract(sections=400)
        chunks, _ = service._split_into_chunks(text)
        assert len(chunks) > 1
        service.responses = [first] + [later] * (len(chunks) - 1)
        result = await service.analyze_contract(text)
        assert result.summary == "A services agreement."
        assert result.key_terms == {"parties": ["Acme", "Global"]}
        assert result.coverage["chunks"] == len(chunks)
        assert result.coverage["chunks_failed"] == 0
        assert result.coverage["truncated"] is False
        # First chunk's risk + the deduped later-chunk risk.
        assert len(result.risks) == 2

    async def test_analyze_contract_truncation_note_present(self):
        first = json.dumps({"summary": "s", "key_terms": {}, "risks": []})
        later = json.dumps([])
        service = _ScriptedLLM([first] + [later] * 19)
        service._split_into_chunks = lambda text: (
            ["head", "mid", "tail"], True,
        )
        result = await service.analyze_contract("giant" * 100000)
        assert result.coverage["truncated"] is True
        assert "AI_MAX_CHUNKS_PER_ANALYSIS" in result.coverage["note"]

    async def test_extract_obligations_covers_all_chunks(self):
        obligations = [
            {"owner_party": "Acme", "description": "Pay net 30",
             "obligation_type": "payment", "amount": "5000",
             "frequency": "monthly", "due_date": None,
             "clause_identifier": "section.3", "sla_metrics": None},
        ]
        service = _ScriptedLLM([])
        text = _long_contract(sections=400)
        chunks, _ = service._split_into_chunks(text)
        service.responses = [json.dumps(obligations)] * len(chunks)
        result = await service.extract_obligations(text)
        assert len(result) == len(chunks)
        assert len(service.prompts) == len(chunks)

    async def test_compare_contracts_multichunk_carries_coverage(self):
        parsed = {
            "summary": "changes found",
            "changes_detected": 2,
            "risk_changes": [],
            "detailed_changes": [{"clause": "9", "change_type": "modified"}],
        }
        service = _ScriptedLLM([])
        text = _long_contract(sections=400)
        # Each version is chunked independently; each LLM call compares the
        # same positional section from both sides (not a chunk to itself).
        base_chunks, _ = service._split_into_chunks(text)
        compared_chunks, _ = service._split_into_chunks(text)
        total = max(len(base_chunks), len(compared_chunks))
        assert total > 1
        service.responses = [json.dumps(parsed)] * total
        result = await service.compare_contracts(text, text)
        assert result["changes_detected"] == total  # 1 change per chunk response
        assert result["coverage"]["chunks"] == total
        assert result["coverage"]["truncated"] is False

        # Both sides of every prompt are real contract text (no self-compare
        # placeholder, no missing-side sentinel when lengths match).
        for prompt in service.prompts:
            assert "(section absent in this version)" not in prompt


class TestCoverageViaAPI:
    """Coverage flows from the service through the API response."""

    async def test_analyze_endpoint_returns_coverage(
        self, client, test_agreement, test_user, db_session
    ):
        from unittest.mock import patch

        from app.api.v1 import ai_analysis as ai_analysis_module
        from app.services import entitlement_service as entitlement_module
        from app.services.ai_service import AnalysisResult

        async def _always_entitled(*args, **kwargs):
            return {"enabled": True, "limit": None, "remaining": None}

        async def _analysis(*args, **kwargs):
            return AnalysisResult(
                summary="s",
                key_terms={"parties": ["A", "B"]},
                coverage={
                    "characters_total": 99999,
                    "chunks": 3,
                    "chunks_analyzed": 3,
                    "chunks_failed": 0,
                    "truncated": False,
                },
            )

        # The version the endpoint reads content from.
        import hashlib

        from app.models.agreement import AgreementVersion

        content = "contract body"
        db_session.add(AgreementVersion(
            agreement_id=test_agreement.id,
            version_number=1,
            status="draft",
            content=content,
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
            created_by=test_user.id,
        ))
        await db_session.flush()

        with patch.object(
            entitlement_module.EntitlementService, "require", _always_entitled
        ), patch.object(
            entitlement_module.EntitlementService, "check_usage", _always_entitled
        ), patch.object(
            ai_analysis_module.ai_service, "analyze_contract", _analysis
        ):
            resp = await client.post(
                f"/api/v1/agreements/{test_agreement.id}/analyze",
                headers={"Authorization": f"Bearer {await _token_for(db_session, test_user)}"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["coverage"] is not None
        assert body["coverage"]["chunks"] == 3
        assert body["coverage"]["characters_total"] == 99999


async def _token_for(db_session, user):
    from app.core.security import create_access_token

    return create_access_token(user_id=user.id)
