"""Virus / malicious-content scan service (spec gap fix).

Architecture
------------
Pluggable adapter pattern.  Two backends are supported:

1. **ClamAV** (``CLAMAV_HOST`` env var set)
   - Uses ``pyclamd`` to stream bytes to a running clamd daemon.
   - Returns the virus name on hit.

2. **Magic-byte deny-list** (default / fallback)
   - Blocks known executable and macro-enabled formats by inspecting the
     first 8 bytes of the file.
   - Zero external dependencies — safe to use even without ClamAV.

Usage
-----
    from app.services.virus_scan_service import scan_bytes, ScanResult

    result = scan_bytes(content)
    if result.infected:
        raise HTTPException(422, f"File rejected: {result.threat}")
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class ScanResult:
    infected: bool
    threat: str | None = None
    backend: str = "none"
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Magic-byte deny-list
# ---------------------------------------------------------------------------

# (prefix_bytes, threat_label)
_MAGIC_DENY_LIST: list[tuple[bytes, str]] = [
    # Windows executables
    (b"MZ", "Windows PE executable"),
    # ELF binaries (Linux/Unix)
    (b"\x7fELF", "ELF executable"),
    # Mach-O (macOS)
    (b"\xfe\xed\xfa\xce", "Mach-O 32-bit"),
    (b"\xce\xfa\xed\xfe", "Mach-O 32-bit (reverse)"),
    (b"\xfe\xed\xfa\xcf", "Mach-O 64-bit"),
    (b"\xcf\xfa\xed\xfe", "Mach-O 64-bit (reverse)"),
    # Java class
    (b"\xca\xfe\xba\xbe", "Java class file"),
    # Python compiled
    (b"\x16\r\r\n", "Python bytecode"),
    (b"\x0d\r\r\n", "Python bytecode (alt)"),
    # ZIP-based macro-enabled Office documents
    # (DOCM / XLSM / PPTM all start with PK\x03\x04 — same as regular OOXML,
    # so we cannot reliably block them here without full ZIP inspection;
    # we block the legacy VBA compound-doc format instead)
    # OLE2 compound document — legacy .doc/.xls/.ppt (may contain VBA)
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "OLE2 compound document (possible macro)"),
    # EICAR test string
    (b"X5O!P%@AP[4\\PZX54(P^)7CC)7}", "EICAR test file"),
    # Shell scripts are not blocked here (too broad) but callers can restrict
    # mime types upstream.
]

_MAGIC_WINDOW = max(len(prefix) for prefix, _ in _MAGIC_DENY_LIST)


def _scan_magic_bytes(content: bytes) -> ScanResult:
    """Scan using the magic-byte deny-list."""
    header = content[:_MAGIC_WINDOW]
    for prefix, label in _MAGIC_DENY_LIST:
        if header[: len(prefix)] == prefix:
            return ScanResult(infected=True, threat=label, backend="magic_bytes")
    return ScanResult(infected=False, backend="magic_bytes")


# ---------------------------------------------------------------------------
# ClamAV adapter
# ---------------------------------------------------------------------------

def _scan_clamav(content: bytes, host: str, port: int = 3310) -> ScanResult:
    """Stream ``content`` to clamd and return the result."""
    try:
        import pyclamd  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "pyclamd is required for ClamAV scanning. Install it or unset CLAMAV_HOST."
        ) from exc

    cd = pyclamd.ClamdNetworkSocket(host=host, port=port, timeout=15)
    try:
        cd.ping()
    except Exception as exc:
        raise RuntimeError(f"Cannot reach ClamAV at {host}:{port}: {exc}") from exc

    result = cd.scan_stream(content)
    if result is None:
        return ScanResult(infected=False, backend="clamav")

    # pyclamd returns {None: ('FOUND', 'Eicar-Signature')} on hit
    for _key, (status, threat) in result.items():
        if status == "FOUND":
            return ScanResult(infected=True, threat=threat, backend="clamav")

    return ScanResult(infected=False, backend="clamav")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_bytes(content: bytes) -> ScanResult:
    """Scan raw bytes for malicious content.

    Automatically chooses the ClamAV backend when ``CLAMAV_HOST`` is set in
    the environment, otherwise falls back to the magic-byte deny-list.

    Parameters
    ----------
    content:
        Raw file bytes to scan.

    Returns
    -------
    ScanResult
        ``.infected`` is ``True`` if the file should be rejected.
    """
    clamav_host = os.environ.get("CLAMAV_HOST", "").strip()
    if clamav_host:
        port = int(os.environ.get("CLAMAV_PORT", "3310"))
        return _scan_clamav(content, host=clamav_host, port=port)
    return _scan_magic_bytes(content)


async def scan_bytes_async(content: bytes) -> ScanResult:
    """Async wrapper — runs the scan in a thread pool to avoid blocking the event loop."""
    import asyncio

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, scan_bytes, content)
