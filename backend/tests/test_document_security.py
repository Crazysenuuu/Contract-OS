"""Document security tests (spec 1.22.26-1.22.34)."""

import pytest

from app.core.exceptions import RateLimitedError, ValidationError
from app.services.document_security import (
    DLPInspector,
    MassDownloadDetector,
    MalwareScanner,
    scan_upload,
)


EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*" + b"padding" * 10


class TestMalwareScan:
    def test_eicar_is_detected(self):
        verdict = MalwareScanner().scan(EICAR)
        assert not verdict.clean
        assert verdict.signature == "eicar_test_file"

    def test_executables_are_detected(self):
        assert not MalwareScanner().scan(b"MZ\x90\x00\x03" + b"\x00" * 200).clean
        assert not MalwareScanner().scan(b"\x7fELF" + b"\x00" * 200).clean

    def test_clean_pdf_passes(self):
        verdict = MalwareScanner().scan(b"%PDF-1.7\n% fake pdf body\n")
        assert verdict.clean

    def test_external_scanner_is_consulted(self):
        scanner = MalwareScanner()

        def custom(data: bytes):
            return None  # external engine has no opinion

        scanner.register_scanner(custom)
        assert scanner.scan(b"%PDF-1.7 ok").clean


class TestDLP:
    def test_private_key_blocked(self):
        verdict = DLPInspector().inspect(b"-----BEGIN RSA PRIVATE KEY-----\nMIIEpA")
        assert not verdict.allowed
        assert verdict.findings[0]["rule"] == "private_key_block"

    def test_aws_key_blocked(self):
        verdict = DLPInspector().inspect(b"key = AKIAIOSFODNN7EXAMPLE")
        assert not verdict.allowed

    def test_normal_contract_passes(self):
        text = b"""
        This Agreement is made between Acme Ltd and Global Buyer Inc.
        The total consideration is USD 50,000 payable within 30 days.
        Governed by the laws of Sri Lanka.
        """
        assert DLPInspector().inspect(text).allowed

    def test_alert_only_mode_allows_but_reports(self):
        dlp = DLPInspector(enabled=True, block_on="never")
        verdict = dlp.inspect(b"-----BEGIN RSA PRIVATE KEY-----")
        assert verdict.allowed
        assert verdict.findings


class TestMassDownload:
    def test_burst_is_blocked(self):
        guard = MassDownloadDetector(max_downloads=5, window_seconds=60)
        for _ in range(5):
            guard.check("user-1")  # must not raise
        with pytest.raises(RateLimitedError):
            guard.check("user-1")

    def test_users_are_isolated(self):
        guard = MassDownloadDetector(max_downloads=3, window_seconds=60)
        for _ in range(3):
            guard.check("user-a")
        guard.check("user-b")  # different user unaffected

    def test_window_allows_recovery(self, monkeypatch):
        import time as time_mod

        real_monotonic = time_mod.monotonic
        offset = [0.0]

        def fake_monotonic():
            return real_monotonic() + offset[0]

        guard = MassDownloadDetector(max_downloads=2, window_seconds=1)
        guard.check("u")
        guard.check("u")
        with pytest.raises(RateLimitedError):
            guard.check("u")
        # Simulate window passage.
        monkeypatch.setattr(time_mod, "monotonic", fake_monotonic)
        offset[0] = 5.0
        guard.check("u")  # window slid - allowed again

    def test_reset(self):
        guard = MassDownloadDetector(max_downloads=1, window_seconds=60)
        guard.check("u")
        guard.reset("u")
        guard.check("u")


class TestUploadGate:
    def test_scan_upload_blocks_malware(self):
        with pytest.raises(ValidationError):
            scan_upload(EICAR)

    def test_scan_upload_blocks_dlp(self):
        with pytest.raises(ValidationError):
            scan_upload(b"-----BEGIN RSA PRIVATE KEY-----")

    def test_scan_upload_passes_clean_content(self):
        result = scan_upload(b"%PDF-1.7 ordinary contract text")
        assert result["malware"]["clean"]
        assert result["dlp"]["allowed"]
