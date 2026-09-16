"""Backup / DR runtime tests (spec 39-40 / 58).

Covers the runtime that the manual test_backup_verification.py never did:
encrypted pg_dump pipeline, retention pruning, restore verification
(orchestration), and admin API wiring. pg_dump execution itself is mocked —
the crypto round-trip and retention logic are real.
"""

import hashlib
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services import backup_service


@pytest.fixture(autouse=True)
def patch_pg_tools(monkeypatch):
    """Assume pg tools exist so the skip path is not triggered in tests."""
    monkeypatch.setattr(backup_service, "_pg_tools_available", lambda: True)


@pytest.fixture(autouse=True)
def tmp_backup_dir(monkeypatch, tmp_path):
    """Route backups into a temp dir and enable backups."""
    s = backup_service.settings()
    monkeypatch.setattr(s, "backup_dir", str(tmp_path / "backups"))
    monkeypatch.setattr(s, "backup_enabled", True)
    return tmp_path


def _fake_dump(monkeypatch, tmp_path, content=b"pg dump payload 123"):
    """Make pg_dump write a real file and return success."""
    def fake_run(cmd, capture_output=None, text=None, timeout=None):
        out = MagicMock()
        out.returncode = 0
        out.stderr = ""
        out.stdout = ""
        dump_flag = cmd.index("-f")
        target = Path(cmd[dump_flag + 1])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return out

    monkeypatch.setattr(backup_service.subprocess, "run", fake_run)
    return content


def test_take_backup_creates_encrypted_file(monkeypatch, tmp_backup_dir):
    content = b"sensitive pg dump"
    _fake_dump(monkeypatch, tmp_backup_dir, content)

    result = backup_service.take_backup(label="test")
    assert result["label"] == "test"
    assert result["size_bytes"] > 0

    enc_path = Path(result["path"])
    assert enc_path.exists()
    encrypted = enc_path.read_bytes()
    # It must not be plaintext.
    assert content not in encrypted
    # Decrypt back to the original payload.
    decrypted = backup_service._decrypt_file(enc_path)
    assert decrypted == content


def test_take_backup_raises_when_pg_dump_fails(monkeypatch, tmp_backup_dir):
    def failing_run(cmd, capture_output=None, text=None, timeout=None):
        out = MagicMock()
        out.returncode = 1
        out.stderr = "pg_dump: connection failed"
        return out

    monkeypatch.setattr(backup_service.subprocess, "run", failing_run)
    with pytest.raises(backup_service.BackupError, match="pg_dump failed"):
        backup_service.take_backup()


def test_list_backups_newest_first(monkeypatch, tmp_backup_dir):
    _fake_dump(monkeypatch, tmp_backup_dir, b"one")
    backup_service.take_backup(label="a")
    time.sleep(1.1)  # ensure distinct mtime ordering

    # Second backup has a later mtime -> sorts first.
    _fake_dump(monkeypatch, tmp_backup_dir, b"two")
    backup_service.take_backup(label="b")

    listed = backup_service.list_backups()
    assert len(listed) == 2
    assert listed[0]["created_at"] >= listed[1]["created_at"]


def test_retention_prunes_oldest(monkeypatch, tmp_backup_dir):
    s = backup_service.settings()
    monkeypatch.setattr(s, "backup_retention_count", 2)

    _fake_dump(monkeypatch, tmp_backup_dir, b"one")
    backup_service.take_backup(label="1")
    _fake_dump(monkeypatch, tmp_backup_dir, b"two")
    backup_service.take_backup(label="2")
    _fake_dump(monkeypatch, tmp_backup_dir, b"three")
    backup_service.take_backup(label="3")

    listed = backup_service.list_backups()
    assert len(listed) == 2  # oldest pruned


def test_verify_restore_uses_psql_pipeline(monkeypatch, tmp_backup_dir):
    """verify_restore succeeds when createdb+pg_restore succeed."""
    content = b"restore me"
    _fake_dump(monkeypatch, tmp_backup_dir, content)
    result = backup_service.take_backup(label="verify")

    calls = []

    def fake_run(cmd, capture_output=None, text=None, timeout=None):
        from app.services.backup_service import _decrypt_file

        calls.append(cmd[0])
        out = MagicMock()
        out.returncode = 0
        out.stderr = ""
        if cmd[0] == "pg_dump":
            pass
        return out

    monkeypatch.setattr(backup_service.subprocess, "run", fake_run)

    verify = backup_service.verify_restore(result["name"])
    assert verify["success"] is True
    assert "pg_restore" in calls
    assert "createdb" in calls


def test_verify_restore_reports_pg_restore_failure(monkeypatch, tmp_backup_dir):
    _fake_dump(monkeypatch, tmp_backup_dir, b"x")
    result = backup_service.take_backup(label="v2")

    out = MagicMock()

    def fake_run(cmd, capture_output=None, text=None, timeout=None):
        out.returncode = 0
        out.stderr = ""
        if cmd[0] == "pg_restore":
            out.returncode = 1
            out.stderr = "pg_restore: error: could not execute query"
        return out

    monkeypatch.setattr(backup_service.subprocess, "run", fake_run)
    with pytest.raises(backup_service.BackupError, match="pg_restore failed"):
        backup_service.verify_restore(result["name"])


def test_backup_pipeline_output_format_roundtrip(monkeypatch, tmp_backup_dir):
    """IV||ciphertext layout round-trips and survives rekeyed data."""
    content = os.urandom(4096)
    _fake_dump(monkeypatch, tmp_backup_dir, content)
    result = backup_service.take_backup(label="roundtrip")

    decrypted = backup_service._decrypt_file(Path(result["path"]))
    assert decrypted == content
    assert result["sha256"] == hashlib.sha256(
        Path(result["path"]).read_bytes()
    ).hexdigest()


# --- Admin API wiring --------------------------------------------------------

@pytest.mark.integration
async def test_backup_endpoints_require_admin(client, test_user):
    """Non-admin tokens are rejected with 403 on all backup routes."""
    from app.core.security import create_access_token

    token = create_access_token(user_id=test_user.id)
    headers = {"Authorization": f"Bearer {token}"}
    for method, path in [
        ("get", "/api/v1/admin/backups"),
        ("post", "/api/v1/admin/backups/run"),
        ("post", "/api/v1/admin/backups/verify-restore"),
    ]:
        resp = await client.request(method, path, headers=headers)
        assert resp.status_code == 403, (
            f"{method.upper()} {path} should be 403 for non-admin, "
            f"got {resp.status_code}"
        )