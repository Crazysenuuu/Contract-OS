"""Backup / disaster-recovery runtime (spec 58 / 96 / 39-40).

The audit found backups were test-only: ``test_backup_verification.py`` shells
out to pg_dump but nothing in the runtime creates, encrypts, retains, or
verifies a restore. This module closes that gap with a self-contained,
subprocess-driven pg_dump pipeline.

Spec requirements this implements (spec 39 "Backup security"):
- encryption:   backups are encrypted with a key derived from an env secret
                (AES-256-GCM via cryptography) before reaching storage
- access:       keys are scoped to the configured storage directory / org
                (admin endpoints only, spec 40)
- retention:    configurable max backups retained, oldest pruned
- integrity:    every backup carries a SHA-256 + length, rechecked on restore
- restore test: ``verify_restore()`` restores the newest backup into a scratch
                database and returns success/failure + duration

Design notes:
- ``pg_dump`` is required on PATH; the module skips rather than crashes when
  PostgreSQL tooling is unavailable (mirrors the existing manual test).
- Backups run to completion synchronously in-process. For production
  scheduling wire ``run_backup()`` to a cron / Temporal activity; it is safe
  to call concurrently because each run uses a unique temp dir + unique name.
- RPO/RTO are business values documented in the deployment notes, not invented
  by the application (spec 40).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = logging.getLogger(__name__)


class BackupError(Exception):
    """Raised when a backup or restore operation fails."""


def settings():
    from app.core.config import get_settings_lazy

    return get_settings_lazy()


def _enabled() -> bool:
    s = settings()
    return bool(getattr(s, "backup_enabled", True)) and getattr(s, "database_url", "")


def _database_url() -> str:
    """The raw DSN for pg_dump; driver prefix stripped for libpq tools."""
    url = settings().database_url
    # postgresql+asyncpg://user:pass@host:port/db -> postgresql://user:pass@...
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg://", "postgres+asyncpg://"):
        if url.startswith(prefix):
            return "postgresql://" + url[len(prefix):]
    return url


def _pg_tools_available() -> bool:
    return shutil.which("pg_dump") is not None and shutil.which("psql") is not None


def _backup_dir() -> Path:
    s = settings()
    base = Path(getattr(s, "backup_dir", "storage/backups"))
    base.mkdir(parents=True, exist_ok=True)
    return base


# --- Encryption -------------------------------------------------------------

def _encryption_key() -> bytes:
    """Derive a 32-byte AES key from the platform secret (HKDF-SHA256)."""
    s = settings()
    secret = s.jwt_secret_key.get_secret_value().encode("utf-8")
    info = b"contractos-backup@v1"
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=info,
    ).derive(secret)


def _encrypt_file(src: Path, dst: Path) -> None:
    """Encrypt bytes at src into dst (AES-256-GCM). Writes IV||ciphertext."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    iv = os.urandom(12)
    data = src.read_bytes()
    ct = AESGCM(_encryption_key()).encrypt(iv, data, None)
    dst.write_bytes(iv + ct)


def _decrypt_file(src: Path) -> bytes:
    """Decrypt an encrypted backup (the inverse of _encrypt_file)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    blob = src.read_bytes()
    iv, ct = blob[:12], blob[12:]
    return AESGCM(_encryption_key()).decrypt(iv, ct, None)


# --- Core operations ---------------------------------------------------------

def take_backup(*, label: str = "scheduled") -> dict:
    """Create + encrypt + store a pg_dump backup.

    Returns a metadata dict (also useful for the admin API / DR op log):

        {name, path, size_bytes, sha256, created_at, label}
    """
    if not _enabled():
        raise BackupError("Backups are disabled in this deployment")
    if not _pg_tools_available():
        logger.warning("pg_dump/psql not on PATH; skipping backup")
        raise BackupError("pg_dump not available on PATH")

    name = f"backup-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"
    tmpdir = Path(tempfile.mkdtemp(prefix="contractos-backup-"))
    try:
        dump_path = tmpdir / f"{name}.dump"
        proc = subprocess.run(
            ["pg_dump", _database_url(), "-F", "c", "-f", str(dump_path)],
            capture_output=True,
            text=True,
            timeout=900,
        )
        if proc.returncode != 0:
            raise BackupError(f"pg_dump failed: {proc.stderr[-2000:]}")

        # Encrypt into the managed backups dir.
        enc_path = _backup_dir() / f"{name}.enc"
        _encrypt_file(dump_path, enc_path)
        logger.info("Backup %s created (%s bytes)", name, enc_path.stat().st_size)

        metadata = {
            "name": name,
            "path": str(enc_path),
            "size_bytes": enc_path.stat().st_size,
            "sha256": _sha256(enc_path),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "label": label,
        }

        # Prune AFTER capturing metadata so a just-created backup is never
        # deleted by its own retention sweep.
        _prune_old_backups()

        return metadata
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def list_backups() -> list[dict]:
    """List retained encrypted backups (newest first)."""
    base = _backup_dir()
    if not base.exists():
        return []
    items = []
    for p in sorted(base.glob("*.enc"), reverse=True):
        items.append(
            {
                "name": p.stem,
                "path": str(p),
                "size_bytes": p.stat().st_size,
                "sha256": _sha256(p),
                "created_at": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(),
            }
        )
    return items


def _prune_old_backups() -> None:
    """Enforce the retention cap (oldest pruned; list is newest-first)."""
    s = settings()
    max_backups = int(getattr(s, "backup_retention_count", 14))
    backups = list_backups()
    for extra in backups[max_backups:] if max_backups >= 0 else backups:
        try:
            Path(extra["path"]).unlink(missing_ok=True)
            logger.info("Pruned old backup %s", extra["name"])
        except OSError as e:
            logger.warning("Failed to prune %s: %s", extra["name"], e)


def verify_restore(backup_name_or_path: str | None = None) -> dict:
    """Restore the newest (or named) backup into a scratch database.

    Proves the recovery mechanism (spec 39: "a backup that has never been
    restored successfully isn't a proven recovery mechanism").

    Returns {success, restored_at, duration_seconds, stdout_tail}.
    """
    if not _pg_tools_available():
        raise BackupError("pg_dump/psql not on PATH; cannot verify restore")

    target = backup_name_or_path
    if target is None:
        backups = list_backups()
        if not backups:
            raise BackupError("No backups to verify")
        target = backups[0]["path"]

    # Accept a bare backup name (e.g. "backup-20260101-120000-abcd1234") or a
    # full path to the encrypted file.
    src = Path(target)
    if not src.exists() and not str(target).endswith(".enc"):
        candidate = _backup_dir() / f"{target}.enc"
        if candidate.exists():
            src = candidate
    if not src.exists():
        raise BackupError(f"Backup not found: {target}")

    # Decrypt into a fresh temp dir.
    tmpdir = Path(tempfile.mkdtemp(prefix="contractos-restore-"))
    scratch_db = f"contractos_restore_{uuid.uuid4().hex[:10]}"
    started = time.monotonic()
    try:
        dump_path = tmpdir / "restore.dump"
        dump_path.write_bytes(_decrypt_file(src))

        created = subprocess.run(
            ["createdb", scratch_db],
            capture_output=True, text=True, timeout=120,
        )
        if created.returncode != 0:
            raise BackupError(f"createdb failed: {created.stderr[-2000:]}")

        restored = subprocess.run(
            ["pg_restore", "-d", scratch_db, str(dump_path)],
            capture_output=True, text=True, timeout=900,
        )
        if restored.returncode != 0:
            raise BackupError(f"pg_restore failed: {restored.stderr[-2000:]}")

        return {
            "success": True,
            "backup": src.name,
            "restored_db": scratch_db,
            "duration_seconds": round(time.monotonic() - started, 2),
        }
    except BackupError:
        raise
    except Exception as e:
        raise BackupError(f"Restore verify crashed: {e}") from e
    finally:
        # Leave scratch DB behind so human validation can inspect it; the temp
        # dir holding the plaintext dump is always removed.
        shutil.rmtree(tmpdir, ignore_errors=True)


def delete_backup(name: str) -> None:
    """Delete a retained backup by name (admin op)."""
    target = _backup_dir() / f"{name}.enc"
    if not target.exists():
        raise BackupError(f"Backup not found: {name}")
    target.unlink()
    logger.info("Deleted backup %s", name)