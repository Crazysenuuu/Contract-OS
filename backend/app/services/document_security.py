"""Document security layer (spec 1.22.26-1.22.34).

Three protections over the document repository:

1. Malware scanning (1.22.26): signature-based EICAR + executable-marker
   detection with a pluggable scanner interface (a ClamAV/ICAP daemon can
   be added behind ``MalwareScanner`` without touching callers).
2. DLP inspection (1.22.27): blocks uploads containing raw secrets/PII
   patterns (private keys, AWS keys, credit cards) that must never enter
   the legal repository.
3. Mass-download protection (1.22.32): per-user sliding-window counter on
   document downloads; exceeding the threshold raises a security event.

All checks fail closed for high-confidence hits and every detection is
returned as a structured verdict so callers can audit it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from app.core.exceptions import RateLimitedError, ValidationError


# --- 1.22.26 Malware scanning ---------------------------------------------

# EICAR test file - the industry-standard antivirus test signature.
_EICAR_PREFIX = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"

_MALWARE_SIGNATURES: list[tuple[str, bytes]] = [
    ("eicar_test_file", _EICAR_PREFIX),
    # Windows PE executables must never enter the document repository.
    ("windows_executable", b"MZ\x90\x00\x03\x00\x00\x00"),
    ("elf_executable", b"\x7fELF"),
    ("macos_macho", b"\xcf\xfa\xed\xfe"),
]


@dataclass
class ScanVerdict:
    clean: bool
    signature: str | None = None
    scanner: str = "signature"


class MalwareScanner:
    """Pluggable scanner interface. Default implementation is the built-in
    signature scanner; deployments can register an external engine (ClamAV
    daemon, ICAP) via ``register_scanner``."""

    def __init__(self):
        self._external: Callable[[bytes], ScanVerdict] | None = None

    def register_scanner(self, engine: Callable[[bytes], ScanVerdict]) -> None:
        self._external = engine

    def scan(self, data: bytes) -> ScanVerdict:
        if self._external is not None:
            verdict = self._external(data)
            if verdict is not None:
                return verdict
        for name, signature in _MALWARE_SIGNATURES:
            if data[:4096].startswith(signature) or signature in data[:8192]:
                return ScanVerdict(clean=False, signature=name)
        return ScanVerdict(clean=True)


# --- 1.22.27 DLP inspection ------------------------------------------------

_DLP_RULES: list[tuple[str, re.Pattern[str], str]] = [
    (
        "private_key_block",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
        "high",
    ),
    (
        "aws_access_key",
        re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
        "high",
    ),
    (
        "credit_card",
        re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
        "medium",
    ),
    (
        "national_id_generic",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "medium",
    ),
]


@dataclass
class DLPVerdict:
    allowed: bool
    findings: list[dict] = field(default_factory=list)


class DLPInspector:
    """Content inspection applied to every ingest (spec 1.22.27)."""

    def __init__(self, *, enabled: bool = True, block_on: str = "high"):
        self.enabled = enabled
        self.block_on = block_on  # 'high' | 'medium' | 'never' (alert-only)

    _SEVERITY_ORDER = {"medium": 1, "high": 2}

    def inspect(self, data: bytes) -> DLPVerdict:
        if not self.enabled:
            return DLPVerdict(allowed=True)
        try:
            text = data.decode("utf-8", errors="ignore")
        except Exception:
            return DLPVerdict(allowed=True)

        findings: list[dict] = []
        for name, pattern, severity in _DLP_RULES:
            matches = pattern.findall(text)
            if matches:
                findings.append(
                    {
                        "rule": name,
                        "severity": severity,
                        "count": len(matches),
                        "sample": str(matches[0])[:8] + "...",
                    }
                )

        if self.block_on == "never":
            return DLPVerdict(allowed=True, findings=findings)
        threshold = self._SEVERITY_ORDER.get(self.block_on, 2)
        blocking = [f for f in findings if self._SEVERITY_ORDER[f["severity"]] >= threshold]
        return DLPVerdict(allowed=not blocking, findings=findings)


# --- 1.22.32 Mass-download protection ---------------------------------------

class MassDownloadDetector:
    """Sliding-window per-user download counter.

    Exceeding ``max_downloads`` within ``window_seconds`` raises
    RateLimitedError (429) and reports the burst so the security event
    pipeline can alert on it (spec 1.22.32).

    Counters live in the shared StateStore (Redis when REDIS_URL is set) so
    the threshold holds across API replicas — per-process counters would
    each see only a fraction of a burst behind a load balancer.
    """

    def __init__(self, *, max_downloads: int = 100, window_seconds: int = 300):
        self.max_downloads = max_downloads
        self.window_seconds = window_seconds

    def check(self, user_id: str) -> dict:
        from app.core.distributed_state import get_state_store

        key = f"dlp:downloads:{user_id}"
        stats = get_state_store().window_add(
            key,
            window_seconds=self.window_seconds,
            max_events=self.max_downloads,
        )
        if stats["exceeded"]:
            raise RateLimitedError(
                "Download rate limit exceeded; account flagged for review",
                details={
                    "downloads_in_window": stats["count"],
                    "window_seconds": self.window_seconds,
                },
            )
        return {"downloads_in_window": stats["count"], "limit": self.max_downloads}

    def reset(self, user_id: str) -> None:
        from app.core.distributed_state import get_state_store

        get_state_store().window_reset(f"dlp:downloads:{user_id}")


# --- Shared singletons -------------------------------------------------------

_scanner = MalwareScanner()
_dlp = DLPInspector()
_download_guard = MassDownloadDetector()


def get_malware_scanner() -> MalwareScanner:
    return _scanner


def get_dlp_inspector() -> DLPInspector:
    return _dlp


def get_download_guard() -> MassDownloadDetector:
    return _download_guard


def scan_upload(data: bytes) -> dict:
    """Full upload gate: malware scan then DLP. Raises ValidationError on a
    positive malware verdict or blocking DLP finding. Returns the verdicts
    so the caller can record them on the document metadata."""
    malware = _scanner.scan(data)
    if not malware.clean:
        raise ValidationError(
            "Upload rejected by malware scan",
            details={"scanner": malware.scanner, "signature": malware.signature},
        )
    dlp = _dlp.inspect(data)
    if not dlp.allowed:
        raise ValidationError(
            "Upload rejected by data-loss-prevention policy",
            details={"findings": dlp.findings},
        )
    return {
        "malware": {"clean": malware.clean, "scanner": malware.scanner},
        "dlp": {"allowed": dlp.allowed, "findings": dlp.findings},
    }
