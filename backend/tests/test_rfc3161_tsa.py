"""RFC 3161 timestamp authority tests (spec 1.20.16).

Covers:
- DER TLV encode/parse round-trips and malformed-input rejection.
- TimeStampReq construction (imprint, nonce, certReq).
- TimeStampResp parsing (granted, rejection with failInfo, missing token).
- RFC3161TSA against a scripted HTTP TSA: success path stores a token whose
  imprint matches the requested hash; failures raise.
- Audit batch sealing anchors externally when a TSA issuer is registered,
  degrades to internal_only/anchor_failed without one.
- verify_anchor_token detects imprint mismatches and garbage tokens.
"""

import base64

import pytest

from app.services import rfc3161_tsa
from app.services.rfc3161_tsa import (
    RFC3161TSA,
    build_timestamp_request,
    der_children,
    der_integer,
    der_octet_string,
    der_oid,
    der_seq,
    extract_token_info,
    parse_timestamp_response,
    read_tlv,
    verify_anchor_token,
)

HASH = "ab" * 32  # a SHA-256 hex digest


# ── DER primitives ───────────────────────────────────────────────────────────


class TestDerPrimitives:
    def test_tlv_roundtrip(self):
        blob = der_seq(der_oid("1.2.840.113549.1.9.16.1.4"), der_integer(42))
        tag, content, _, end = read_tlv(blob)
        assert tag == 0x30
        assert end == len(blob)
        kids = der_children(content)
        assert kids[0][0] == 0x06
        assert kids[1][0] == 0x02
        # Without a base, offsets are relative to ``content``.
        assert kids[0][2] == 0

    def test_octet_string_content(self):
        blob = der_octet_string(b"hello")
        tag, content, _, _ = read_tlv(blob)
        assert tag == 0x04 and content == b"hello"

    def test_truncated_input_rejected(self):
        with pytest.raises(rfc3161_tsa.DerError):
            read_tlv(b"\x30\x10ab")

    def test_long_form_length(self):
        big = b"x" * 300
        blob = der_octet_string(big)
        tag, content, _, _ = read_tlv(blob)
        assert tag == 0x04 and content == big


# ── Request building ─────────────────────────────────────────────────────────


class TestTimestampRequest:
    def test_request_contains_imprint_and_nonce(self):
        der = build_timestamp_request(HASH)
        tag, content, _, _ = read_tlv(der)
        assert tag == 0x30
        kids = der_children(content)
        # version, messageImprint, nonce (certReq omitted by default)
        assert len(kids) == 3
        assert kids[0][0] == 0x02
        mi = der_children(kids[1][1])
        # mi elements are (tag, content, start, end) 4-tuples.
        assert mi[0][0] == 0x30
        assert mi[1][0] == 0x04
        assert mi[1][1].hex() == HASH

    def test_requests_are_salted_with_unique_nonces(self):
        a = build_timestamp_request(HASH)
        b = build_timestamp_request(HASH)
        assert a != b, "nonce must differ per request (replay protection)"


# ── Response parsing ─────────────────────────────────────────────────────────


def _pki_status(status: int, fail_info: bytes | None = None) -> bytes:
    from app.services.rfc3161_tsa import der_tlv

    parts = [der_integer(status)]
    if fail_info is not None:
        # bit string: unused-bits byte + content
        parts.append(der_tlv(0x03, b"\x00" + fail_info))
    return der_seq(*parts)


def _make_resp(status: int, token_der: bytes | None = None, fail_info=None) -> bytes:
    # TimeStampResp ::= SEQUENCE { status, timeStampToken OPTIONAL } — the
    # token is a ContentInfo (tag 0x30) placed directly, no [0] wrapper.
    parts = [_pki_status(status, fail_info)]
    if token_der is not None:
        parts.append(token_der)
    return der_seq(*parts)


class TestTimestampResponseParsing:
    def test_granted_without_token(self):
        parsed = parse_timestamp_response(_make_resp(0))
        assert parsed["status"] == 0
        assert parsed["token"] is None

    def test_granted_with_token_preserves_raw_der(self):
        inner = der_seq(der_integer(7))
        resp = _make_resp(0, token_der=inner)
        parsed = parse_timestamp_response(resp)
        # Token bytes are preserved verbatim for offline verification.
        assert parsed["token"] == inner

    def test_rejection_carries_fail_info(self):
        parsed = parse_timestamp_response(_make_resp(2, fail_info=b"\x02\x00"))
        assert parsed["status"] == 2
        assert parsed["fail_info"] == b"\x02\x00"

    def test_pki_status_info_extras_tolerated(self):
        # statusInfo may carry statusString/freeText; must parse fine.
        from app.services.rfc3161_tsa import der_tlv

        resp = der_seq(
            der_seq(der_integer(1), der_tlv(0x16, b"okay")),
        )
        parsed = parse_timestamp_response(resp)
        assert parsed["status"] == 1

    def test_garbage_rejected(self):
        with pytest.raises(rfc3161_tsa.DerError):
            parse_timestamp_response(b"not der")


# ── TSA HTTP client ──────────────────────────────────────────────────────────


class _FakeTSA:
    """httpx transport emulating an RFC 3161 endpoint."""

    def __init__(self, responder):
        self.responder = responder
        self.requests = []

    def handle_request(self, request):
        self.requests.append(request)
        return self.responder(request)


class TestRFC3161TSA:
    def _tsa(self, responder):
        import httpx

        return RFC3161TSA(
            "https://tsa.example/tsr",
            transport=httpx.MockTransport(responder),
        )

    def test_issue_returns_token_on_granted(self):
        # Structurally valid token attesting the requested hash — the client
        # verifies the imprint before accepting it.
        token = _make_token(HASH)

        def responder(request):
            assert request.headers["content-type"] == "application/timestamp-query"
            req = parse_request_imprint(request.content)
            assert req == HASH
            import httpx

            return httpx.Response(200, content=_make_resp(0, token_der=token))

        result = self._tsa(responder).issue(HASH)
        assert base64.b64decode(result) == token

    def test_issue_raises_on_rejection(self):
        import httpx

        def responder(request):
            return httpx.Response(
                200, content=_make_resp(2, fail_info=b"\x03\x00")
            )

        with pytest.raises(RuntimeError, match="refused"):
            self._tsa(responder).issue(HASH)

    def test_issue_raises_on_http_error(self):
        import httpx

        def responder(request):
            return httpx.Response(503, content=b"busy")

        with pytest.raises(RuntimeError, match="HTTP 503"):
            self._tsa(responder).issue(HASH)

    def test_issue_raises_on_transport_failure(self):
        import httpx

        def responder(request):
            raise httpx.ConnectError("network down")

        with pytest.raises(RuntimeError, match="TSA request failed"):
            self._tsa(responder).issue(HASH)

    def test_issue_raises_when_token_imprints_other_hash(self):
        """Misrouted/malicious TSA: token attests a different hash."""
        import httpx

        token = _make_token("cd" * 32)

        def responder(request):
            return httpx.Response(200, content=_make_resp(0, token_der=token))

        with pytest.raises(RuntimeError, match="different hash"):
            self._tsa(responder).issue(HASH)

    def test_issue_raises_when_granted_but_token_missing(self):
        import httpx

        def responder(request):
            return httpx.Response(200, content=_make_resp(0))

        with pytest.raises(RuntimeError, match="no token"):
            self._tsa(responder).issue(HASH)


def parse_request_imprint(req_der: bytes) -> str:
    """Helper: pull the imprint hex out of a TimeStampReq."""
    _, content, _, _ = read_tlv(req_der)
    kids = der_children(content)
    mi = der_children(kids[1][1])
    return mi[1][1].hex()


# ── Token verification ───────────────────────────────────────────────────────


def _make_token(imprint_hex: str, gen_time: bytes = b"20260921120000Z") -> bytes:
    """Minimal but structurally correct CMS TimeStampToken:

    ContentInfo( OID id-ct-TSTInfo, [0] SignedData(
        version, digestAlgorithms SET, encapContentInfo(
            eContentType OID, [0] OCTET STRING TSTInfo ) ) )
    """
    from app.services.rfc3161_tsa import SHA256_OID, der_set, der_tlv

    imprint = der_seq(
        der_seq(der_oid(SHA256_OID)), der_octet_string(bytes.fromhex(imprint_hex))
    )
    # TSTInfo fields per RFC 3161 §2.4.1: version, policy (mandatory OID),
    # messageImprint, serialNumber, genTime.
    tst = der_seq(
        der_integer(1),
        der_oid("1.3.6.1.4.1.13762.3"),  # freetsa policy, any OID parses
        imprint,
        der_integer(5),
        der_tlv(0x18, gen_time),
    )
    signed_data = der_seq(
        der_integer(1),                       # version
        der_set(der_oid(SHA256_OID)),         # digestAlgorithms
        der_seq(                              # encapContentInfo
            der_oid("1.2.840.113549.1.7.1"),  # id-data
            der_tlv(0xA0, der_tlv(0x04, tst)),
        ),
    )
    return der_seq(
        der_oid("1.2.840.113549.1.9.16.1.4"),
        der_tlv(0xA0, signed_data),
    )


class TestTokenVerification:
    def test_matching_imprint_is_valid(self):
        token = base64.b64encode(_make_token(HASH)).decode()
        result = verify_anchor_token(token, HASH)
        assert result["valid"] is True
        assert result["gen_time"] is not None

    def test_mismatched_imprint_is_invalid(self):
        token = base64.b64encode(_make_token("cd" * 32)).decode()
        result = verify_anchor_token(token, HASH)
        assert result["valid"] is False
        assert "does not match" in result["reason"]

    def test_garbage_token_is_invalid_not_fatal(self):
        result = verify_anchor_token("bm90IGRlcg==", HASH)
        assert result["valid"] is False


# ── Audit batch anchoring ────────────────────────────────────────────────────


class TestAuditBatchAnchoring:
    def test_anchor_with_registered_issuer(self):
        from app.services.audit_batching import ExternalTimestampAnchor

        anchor = ExternalTimestampAnchor()
        anchor.set_issuer(lambda root: base64.b64encode(_make_token(root)).decode())
        result = anchor.issue(HASH)
        assert result["anchor_status"] == "externally_anchored"
        assert result["token"] is not None

    def test_anchor_without_issuer_is_honest(self):
        from app.services.audit_batching import ExternalTimestampAnchor

        result = ExternalTimestampAnchor().issue(HASH)
        assert result["anchor_status"] == "internal_only"
        assert result["token"] is None

    def test_anchor_issuer_failure_records_anchor_failed(self):
        from app.services.audit_batching import ExternalTimestampAnchor

        def boom(root):
            raise RuntimeError("TSA down")

        anchor = ExternalTimestampAnchor()
        anchor.set_issuer(boom)
        result = anchor.issue(HASH)
        assert result["anchor_status"] == "anchor_failed"
        assert "TSA down" in result["error"]


@pytest.mark.asyncio
async def test_seal_batch_uses_configured_tsa(
    db_session, test_org, test_user, monkeypatch
):
    """End-to-end: sealed batch stores the TSA token and verify passes."""
    from app.services import audit_batching
    from app.services.audit_batching import AuditBatchService
    from app.services.audit_service import record_event

    from app.services.rfc3161_tsa import der_tlv

    events = []
    for i in range(3):
        events.append(await record_event(
            db_session, tenant_id=test_org.id, actor_id=test_user.id,
            actor_type="user", action=f"TSA_EVENT_{i}",
            resource_type="agreement", metadata_json={"i": i},
        ))
    await db_session.commit()

    captured = {}

    def fake_issuer(root_hash):
        captured["root"] = root_hash
        return base64.b64encode(_make_token(root_hash)).decode()

    monkeypatch.setattr(audit_batching, "get_anchor_service", lambda: (
        (lambda a: (a.set_issuer(fake_issuer), a)[1])(audit_batching.ExternalTimestampAnchor())
    ))

    service = AuditBatchService(db_session)
    batch = await service.seal_batch(
        tenant_id=test_org.id, from_sequence=1, to_sequence=3
    )
    assert batch.anchor_status == "externally_anchored"
    assert batch.anchored_at is not None
    assert captured["root"] == batch.root_hash

    verdict = await service.verify_batch(batch.id)
    assert verdict["valid"] is True
    assert verdict["anchor_status"] == "externally_anchored"

    # The stored token verifies against the batch root offline.
    stored = verify_anchor_token(batch.anchor_token, batch.root_hash)
    assert stored["valid"] is True


@pytest.mark.asyncio
async def test_seal_batch_without_tsa_stays_internal(db_session, test_org, test_user):
    from app.services.audit_batching import AuditBatchService
    from app.services.audit_service import record_event

    await record_event(
        db_session, tenant_id=test_org.id, actor_id=test_user.id,
        actor_type="user", action="NO_TSA_EVENT", resource_type="agreement",
        metadata_json={},
    )
    await db_session.commit()

    service = AuditBatchService(db_session)
    batch = await service.seal_batch(
        tenant_id=test_org.id, from_sequence=1, to_sequence=1
    )
    assert batch.anchor_status == "internal_only"
    assert batch.anchor_token is None
