"""RFC 3161 timestamp authority client (spec 1.20.16).

Audit batches are sealed with a Merkle root (``app.services.audit_batching``).
To make that root *externally witnessed*, the root hash is sent to an RFC
3161 Time-Stamp Authority (TSA): the TSA returns a signed token containing
the hash and its trusted time. The base64 token is stored on the batch row
as the external anchor and can be verified offline later (e.g.
``openssl ts -verify -token_in -data <root file> -CAfile <tsa chain>``).

Protocol (RFC 3161):

    TimeStampReq  -- DER, POSTed as application/timestamp-query
    TimeStampResp -- DER, received as application/timestamp-reply
                     { status PKIStatusInfo, token TimeStampToken OPTIONAL }

The module is dependency-free on purpose: no ``asn1crypto`` is installed,
so the few DER structures we need are encoded/parsed by hand with minimal
TLV helpers and validated by round-trip tests.

Failure contract: ``issue`` raises on any HTTP/protocol error so the
anchor wrapper (``ExternalTimestampAnchor``) records ``anchor_failed``;
it returns a token only when the TSA status is granted *and* the token's
embedded imprint matches the hash we asked to timestamp (defense against
misbehaving or misrouted TSAs).
"""

from __future__ import annotations

import base64
import binascii
import secrets
from datetime import datetime, timezone

import httpx

SHA256_OID = "2.16.840.1.101.3.4.2.1"
TST_INFO_OID = "1.2.840.113549.1.9.16.1.4"  # id-ct-TSTInfo (RFC 3161 §2.4.1)

# PKIStatus (RFC 3161 §2.4.2)
PKI_GRANTED = 0
PKI_GRANTED_WITH_MODS = 1


# --------------------------------------------------------------------------
# Minimal DER encoding
# --------------------------------------------------------------------------


def _der_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def der_tlv(tag: int, content: bytes) -> bytes:
    return bytes([tag]) + _der_len(len(content)) + content


def der_seq(*parts: bytes) -> bytes:
    return der_tlv(0x30, b"".join(parts))


def der_set(*parts: bytes) -> bytes:
    return der_tlv(0x31, b"".join(parts))


def der_integer(value: int) -> bytes:
    if value == 0:
        return der_tlv(0x02, b"\x00")
    body = value.to_bytes((value.bit_length() + 8) // 8, "big", signed=True)
    return der_tlv(0x02, body)


def der_bool(value: bool) -> bytes:
    return der_tlv(0x01, b"\xff" if value else b"\x00")


def der_oid(dotted: str) -> bytes:
    arcs = [int(a) for a in dotted.split(".")]
    body = bytearray([arcs[0] * 40 + arcs[1]])
    for arc in arcs[2:]:
        chunk = [arc & 0x7F]
        arc >>= 7
        while arc:
            chunk.append((arc & 0x7F) | 0x80)
            arc >>= 7
        body.extend(reversed(chunk))
    return der_tlv(0x06, bytes(body))


def der_octet_string(data: bytes) -> bytes:
    return der_tlv(0x04, data)


# --------------------------------------------------------------------------
# Minimal DER parsing (TLV walk)
# --------------------------------------------------------------------------


class DerError(ValueError):
    pass


def read_tlv(data: bytes, offset: int = 0) -> tuple[int, bytes, int, int]:
    """Read one TLV. Returns (tag, content, start, end) with raw offsets so
    callers can slice the raw DER of an element (needed to keep the
    TimeStampToken bytes verbatim)."""
    if offset + 2 > len(data):
        raise DerError("truncated TLV header")
    tag = data[offset]
    first = data[offset + 1]
    pos = offset + 2
    if first < 0x80:
        length = first
    else:
        n = first & 0x7F
        if n == 0 or pos + n > len(data):
            raise DerError("bad long-form length")
        length = int.from_bytes(data[pos : pos + n], "big")
        pos += n
    end = pos + length
    if end > len(data):
        raise DerError("content overruns buffer")
    return tag, data[pos:end], offset, end


def der_children(content: bytes, base: int = 0) -> list[tuple[int, bytes, int, int]]:
    """Parse concatenated TLVs (a SEQUENCE/SET body) into a list.

    ``base`` is the absolute offset of ``content`` within the original DER
    buffer, so returned (start, end) offsets can be used to re-slice the
    raw buffer directly (needed to keep tokens byte-for-byte intact).
    """
    items = []
    offset = 0
    while offset < len(content):
        tag, value, start, end = read_tlv(content, offset)
        items.append((tag, value, start + base, end + base))
        offset = end
    return items


def element_children(buf: bytes, tlv_offset: int) -> list[tuple[int, bytes, int, int]]:
    """Children of the constructed element whose TLV starts at
    ``tlv_offset`` in ``buf``. Returned offsets are absolute in ``buf``."""
    tag, content, start, end = read_tlv(buf, tlv_offset)
    content_abs = end - len(content)
    return der_children(content, base=content_abs)


# --------------------------------------------------------------------------
# TimeStampReq / TimeStampResp
# --------------------------------------------------------------------------


def build_timestamp_request(
    hash_hex: str,
    *,
    nonce: int | None = None,
    policy_oid: str | None = None,
    cert_req: bool = False,
    hash_oid: str = SHA256_OID,
) -> bytes:
    """Build a TimeStampReq over ``hash_hex`` (SHA-256 imprint)."""
    raw = bytes.fromhex(hash_hex)
    if nonce is None:
        nonce = secrets.randbits(63)
    message_imprint = der_seq(
        der_seq(der_oid(hash_oid)),
        der_octet_string(raw),
    )
    parts = [der_integer(1), message_imprint]
    if policy_oid:
        # reqPolicy precedes nonce per the ASN.1 field order.
        parts.append(der_oid(policy_oid))
    parts.append(der_integer(nonce))
    if cert_req:
        parts.append(der_bool(True))
    return der_seq(*parts)


def parse_timestamp_response(der: bytes) -> dict:
    """Parse a TimeStampResp.

    Returns ``{"status": int, "fail_info": bytes|None, "token": bytes|None}``
    where ``token`` is the raw TimeStampToken (ContentInfo) DER when present.
    """
    tag, outer, _, oend = read_tlv(der)
    if tag != 0x30:
        raise DerError(f"TimeStampResp must be a SEQUENCE, got tag 0x{tag:02x}")
    outer_base = oend - len(outer)
    children = der_children(outer, base=outer_base)
    if not children:
        raise DerError("empty TimeStampResp")

    status_tag, status_val, _, _ = children[0]
    if status_tag != 0x30:
        raise DerError("PKIStatusInfo must be a SEQUENCE")
    status_parts = der_children(status_val)
    if not status_parts:
        raise DerError("empty PKIStatusInfo")
    st_tag, st_val, _, _ = status_parts[0]
    if st_tag != 0x02:
        raise DerError("PKIStatus must be an INTEGER")
    status = int.from_bytes(st_val, "big", signed=True)

    fail_info = None
    # PKIStatusInfo ::= SEQUENCE { status, statusString OPTIONAL, failInfo
    # OPTIONAL } — failInfo may sit at index 1 or 2; scan for the BIT STRING.
    for fi_tag, fi_val, _, _ in status_parts[1:]:
        if fi_tag == 0x03:  # BIT STRING: first content byte = unused bits
            fail_info = fi_val[1:] if fi_val else b""
            break

    token = None
    if len(children) > 1:
        # Raw TimeStampToken bytes: children offsets are absolute, so the
        # slice is byte-for-byte intact for offline verification.
        _, _, tok_start, tok_end = children[1]
        token = der[tok_start:tok_end]

    return {"status": status, "fail_info": fail_info, "token": token}


# --------------------------------------------------------------------------
# Token verification (structure + imprint match)
# --------------------------------------------------------------------------


def _oid_to_dotted(raw: bytes) -> str:
    """Decode an OID's content bytes into dotted-decimal notation."""
    if not raw:
        return ""
    first = raw[0]
    arcs = [first // 40, first % 40]
    value = 0
    for byte in raw[1:]:
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            arcs.append(value)
            value = 0
    return ".".join(str(a) for a in arcs)


def _parse_generalized_time(raw: bytes) -> str:
    text = raw.decode("ascii", errors="replace")
    body = text.rstrip("Z")
    if "." in body:
        body = body.split(".", 1)[0]
    try:
        parsed = datetime.strptime(body, "%Y%m%d%H%M%S")
    except ValueError:
        return text
    return parsed.replace(tzinfo=timezone.utc).isoformat()


def extract_token_info(token_der: bytes) -> dict:
    """Walk ContentInfo → SignedData → encapContentInfo → TSTInfo.

    Returns imprint hash, genTime and policy so callers can verify the
    token attests the expected root. Cryptographic signature-chain
    validation (TSA cert) is left to offline tooling — the stored token is
    the verbatim DER for exactly that purpose.
    """
    tag, outer, _, _ = read_tlv(token_der)
    if tag != 0x30:
        raise DerError("TimeStampToken must be a ContentInfo SEQUENCE")
    ci = element_children(token_der, 0)
    if len(ci) < 2 or ci[1][0] != 0xA0:
        raise DerError("ContentInfo missing [0] content")

    # Descend into [0] → SignedData SEQUENCE → its fields.
    zero_children = element_children(token_der, ci[1][2])
    if not zero_children or zero_children[0][0] != 0x30:
        raise DerError("SignedData must be a SEQUENCE")
    sd = element_children(token_der, zero_children[0][2])
    if len(sd) < 3 or sd[2][0] != 0x30:
        raise DerError("SignedData missing encapContentInfo")

    eci = element_children(token_der, sd[2][2])
    if len(eci) < 2 or eci[1][0] != 0xA0:
        raise DerError("encapContentInfo missing [0] eContent")
    # Descend into [0] → the OCTET STRING wrapping the TSTInfo.
    zero_eci = element_children(token_der, eci[1][2])
    if not zero_eci or zero_eci[0][0] != 0x04:
        raise DerError("eContent must contain an OCTET STRING")
    octet_tag, octet_val, oct_start, oct_end = read_tlv(
        token_der, zero_eci[0][2]
    )

    # octet_val holds the raw TSTInfo TLV.
    tst_tag, tst_val, _, _ = read_tlv(octet_val)
    if tst_tag != 0x30:
        raise DerError("TSTInfo must be a SEQUENCE")
    fields = der_children(tst_val)
    if len(fields) < 4:
        raise DerError("TSTInfo truncated")

    # TSTInfo ::= SEQUENCE {
    #   version     INTEGER,          -- index 0
    #   policy      TSA_POLICY_ID,    -- index 1 (mandatory, RFC 3161 §2.4.1)
    #   messageImprint SEQUENCE,      -- index 2
    #   serialNumber   INTEGER,       -- index 3
    #   genTime        GeneralizedTime, -- index 4 (then accuracy/ordering/...
    #                                   --  extensions may follow)
    # }
    if fields[1][0] != 0x06:
        raise DerError("TSTInfo field 1 must be the policy OID")
    policy_oid = _oid_to_dotted(fields[1][1])

    mi_tag, mi_val, _, _ = fields[2]
    if mi_tag != 0x30:
        raise DerError("messageImprint must be a SEQUENCE")
    mi = der_children(mi_val)
    imprint_tag, imprint_val, _, _ = mi[1]
    if imprint_tag != 0x04:
        raise DerError("hashedMessage must be an OCTET STRING")

    gen_time = None
    for gt_tag, gt_val, _, _ in fields[3:]:
        if gt_tag == 0x18:  # GeneralizedTime
            gen_time = _parse_generalized_time(gt_val)
            break

    return {
        "imprint_hash": imprint_val.hex(),
        "gen_time": gen_time,
        "policy": policy_oid,
    }


def verify_anchor_token(token_b64: str, expected_hash_hex: str) -> dict:
    """Verify a stored anchor token attests ``expected_hash_hex``.

    Returns ``{"valid": bool, "gen_time": ..., "reason": ...}``.
    """
    try:
        token_der = base64.b64decode(token_b64)
        info = extract_token_info(token_der)
    except (DerError, binascii.Error, ValueError, IndexError) as exc:
        return {"valid": False, "reason": f"token unreadable: {exc}"}
    matches = info["imprint_hash"] == expected_hash_hex
    return {
        "valid": matches,
        "gen_time": info["gen_time"],
        "reason": None if matches else "token imprint does not match batch root",
    }


# --------------------------------------------------------------------------
# TSA HTTP client
# --------------------------------------------------------------------------


class RFC3161TSA:
    """HTTP TSA client conforming to the audit anchor issuer interface:
    ``issue(root_hash) -> base64 token``; raises on failure."""

    def __init__(
        self,
        url: str,
        *,
        timeout_seconds: float = 10.0,
        username: str | None = None,
        password: str | None = None,
        policy_oid: str | None = None,
        cert_req: bool = False,
        transport: httpx.BaseTransport | None = None,
    ):
        self.url = url
        self.timeout_seconds = timeout_seconds
        self.username = username
        self.password = password
        self.policy_oid = policy_oid
        self.cert_req = cert_req
        self._transport = transport

    def issue(self, root_hash: str) -> str:
        request = build_timestamp_request(
            root_hash, policy_oid=self.policy_oid, cert_req=self.cert_req
        )
        auth = (
            (self.username, self.password)
            if self.username and self.password
            else None
        )
        try:
            client_kwargs = {"timeout": self.timeout_seconds}
            if self._transport is not None:
                client_kwargs["transport"] = self._transport
            with httpx.Client(**client_kwargs) as client:
                response = client.post(
                    self.url,
                    content=request,
                    headers={
                        "Content-Type": "application/timestamp-query",
                        "Accept": "application/timestamp-reply",
                    },
                    auth=auth,
                )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"TSA request failed: {exc}") from exc

        if response.status_code != 200:
            raise RuntimeError(f"TSA returned HTTP {response.status_code}")

        try:
            parsed = parse_timestamp_response(response.content)
        except DerError as exc:
            raise RuntimeError(f"TSA response unparseable: {exc}") from exc

        if parsed["status"] not in (PKI_GRANTED, PKI_GRANTED_WITH_MODS):
            raise RuntimeError(
                f"TSA refused request (status={parsed['status']}, "
                f"fail_info={parsed['fail_info'].hex() if parsed['fail_info'] else 'none'})"
            )
        if parsed["token"] is None:
            raise RuntimeError("TSA granted the request but returned no token")

        # Defense in depth: the token must attest exactly the hash we sent.
        info = extract_token_info(parsed["token"])
        if info["imprint_hash"] != root_hash:
            raise RuntimeError("TSA token imprints a different hash than requested")

        return base64.b64encode(parsed["token"]).decode("ascii")
