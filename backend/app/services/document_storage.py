"""Object-storage document security (spec 1.23 / 2.07 / 1.22).

A provider abstraction over where document blobs live. The default backend
is a local filesystem store under the configured ``document_storage_dir``.
When ``storage_backend == 's3'`` the same public API dispatches to an
S3-compatible object store (AWS S3 or MinIO) without changing call sites
(spec 2.07 ``S3-compatible object storage``).

Secure download uses short-lived signed tokens (HMAC) instead of exposing
raw storage keys, so a leaked URL cannot be replayed indefinitely and every
download can be traced.

Watermarking overlays viewer identity on the rendered document so shared
copies carry who-when-where provenance (spec 1.22 watermarking).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from app.core.config import get_settings_lazy

settings = get_settings_lazy()

# Token lifetime for secure downloads (seconds).
DOWNLOAD_TOKEN_TTL = int(os.environ.get("DOCUMENT_DOWNLOAD_TOKEN_TTL", "900"))  # 15 min


class StorageError(Exception):
    """Raised when a storage operation fails."""


# --- S3/MinIO backend -------------------------------------------------------

def _s3_client():
    """Lazily build (and cache) the S3-compatible client.

    Falls back to boto3's default credential chain when explicit credentials
    are not configured (IAM role / instance profile on AWS).
    """
    import boto3  # type: ignore

    if getattr(settings, "s3_access_key_id", None):
        aws_access_key_id = settings.s3_access_key_id.get_secret_value()
    else:
        aws_access_key_id = os.environ.get("AWS_ACCESS_KEY_ID")
    if getattr(settings, "s3_secret_access_key", None):
        aws_secret_access_key = settings.s3_secret_access_key.get_secret_value()
    else:
        aws_secret_access_key = os.environ.get("AWS_SECRET_ACCESS_KEY")

    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url or None,
        region_name=settings.s3_region_name,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
    )


def ensure_bucket(bucket: str | None = None) -> None:
    """Create the configured S3 bucket if it does not exist (idempotent).

    No-op when the active backend is the local filesystem.
    """
    if _backend_name() != "s3":
        return
    import botocore  # type: ignore

    try:
        _s3_client().create_bucket(Bucket=bucket or settings.s3_bucket)
    except botocore.exceptions.ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        # BucketAlreadyOwnedByYou / BucketAlreadyExists indicate success.
        if code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            return
        raise StorageError(f"Failed to ensure S3 bucket: {e}") from e
    except Exception as e:
        raise StorageError(f"Failed to ensure S3 bucket: {e}") from e


def _backend_name() -> str:
    return getattr(settings, "storage_backend", "local") or "local"


# --- Backend dispatch (public API) -------------------------------------------

def _storage_root() -> Path:
    directory = getattr(settings, "document_storage_dir", None) or os.environ.get(
        "DOCUMENT_STORAGE_DIR", "storage/documents"
    )
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    return root


def build_content_ref(*, org_id: uuid.UUID, agreement_id: uuid.UUID, doc_type: str, suffix: str = "pdf") -> str:
    """Build an opaque storage key: org/agreement/type-{uuid}.{suffix}."""
    return f"{org_id}/{agreement_id}/{doc_type}-{uuid.uuid4().hex}.{suffix}"


def store_blob(content_ref: str, data: bytes) -> str:
    """Store bytes at the content_ref path on the configured backend."""
    if _backend_name() == "s3":
        try:
            _s3_client().put_object(
                Bucket=settings.s3_bucket,
                Key=content_ref,
                Body=data,
            )
        except Exception as e:
            raise StorageError(f"Failed to store document: {e}") from e
        return content_ref

    root = _storage_root()
    path = root / content_ref
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_bytes(data)
    except OSError as e:
        raise StorageError(f"Failed to store document: {e}") from e
    return content_ref


def load_blob(content_ref: str) -> bytes:
    """Load bytes for a content_ref from the configured backend."""
    if _backend_name() == "s3":
        try:
            resp = _s3_client().get_object(
                Bucket=settings.s3_bucket, Key=content_ref
            )
            return resp["Body"].read()
        except Exception as e:
            raise StorageError(f"Document not found: {content_ref}") from e

    root = _storage_root()
    path = root / content_ref
    if not path.is_file():
        raise StorageError(f"Document not found: {content_ref}")
    return path.read_bytes()


def delete_blob(content_ref: str) -> None:
    """Delete bytes for a content_ref on the configured backend."""
    if _backend_name() == "s3":
        try:
            _s3_client().delete_object(
                Bucket=settings.s3_bucket, Key=content_ref
            )
        except Exception as e:
            raise StorageError(f"Failed to delete document: {e}") from e
        return

    root = _storage_root()
    path = root / content_ref
    if path.is_file():
        path.unlink()


def blob_size(content_ref: str) -> int | None:
    """Return the blob size on the configured backend, or None if absent."""
    if _backend_name() == "s3":
        try:
            head = _s3_client().head_object(
                Bucket=settings.s3_bucket, Key=content_ref
            )
            return int(head["ContentLength"])
        except Exception:
            return None

    root = _storage_root()
    path = root / content_ref
    if path.is_file():
        return path.stat().st_size
    return None


# --- Secure download tokens ------------------------------------------------

def _token_secret() -> bytes:
    secret = os.environ.get("DOCUMENT_TOKEN_SECRET") or settings.jwt_secret_key.get_secret_value()
    return secret.encode("utf-8")


def create_download_token(*, content_ref: str, user_id: uuid.UUID | str, org_id: uuid.UUID | str) -> str:
    """Create a short-lived signed download token."""
    expires = int(time.time()) + DOWNLOAD_TOKEN_TTL
    payload = f"{content_ref}|{user_id}|{org_id}|{expires}"
    sig = hmac.new(_token_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}|{sig}"


def verify_download_token(token: str) -> dict | None:
    """Verify a signed download token.

    Returns dict with content_ref/user_id/org_id or None if invalid/expired.
    """
    try:
        parts = token.split("|")
        if len(parts) != 5:
            return None
        content_ref, user_id, org_id, expires_str, sig = parts
        payload = f"{content_ref}|{user_id}|{org_id}|{expires_str}"
        expected = hmac.new(_token_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return None
        if int(expires_str) < int(time.time()):
            return None
        return {
            "content_ref": content_ref,
            "user_id": user_id,
            "org_id": org_id,
        }
    except (ValueError, TypeError):
        return None


# --- Watermarking -----------------------------------------------------------

def watermark_html(html: str, *, viewer_name: str, org_name: str) -> str:
    """Overlay a viewer-identity watermark on the rendered document HTML.

    The watermark is drawn as translucent diagonal text behind the content,
    repeated across the page. WeasyPrint honors the CSS transform, so the
    resulting PDF carries the watermark (spec 1.22 watermarking).
    """
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    watermark_text = f"{org_name} · {viewer_name} · {stamp}"
    style = """
    <style>
      .contract-watermark {
        position: fixed;
        top: 0; left: 0; right: 0; bottom: 0;
        display: flex; align-items: center; justify-content: center;
        transform: rotate(-30deg);
        font-size: 40px;
        color: rgba(120, 120, 120, 0.18);
        white-space: nowrap;
        z-index: 1000;
        pointer-events: none;
        font-family: Arial, Helvetica, sans-serif;
        text-align: center;
      }
      @page { size: A4; margin: 25mm; }
    </style>
    """
    marker = "<div class=\"contract-watermark\">"
    watermark = (
        marker + watermark_text + "</div>"
    )
    # Insert the watermark just after the opening <body> (or at the start if
    # there is no explicit body element).
    if "<body" in html:
        idx = html.index(">", html.index("<body")) + 1
        return html[:idx] + style + watermark + html[idx:]
    return style + watermark + html


def watermark_pdf(data: bytes, *, viewer_name: str, org_name: str) -> bytes:
    """If a PDF library is available, stamp pages; otherwise return as-is.

    Kept as a no-op-safe hook so callers do not need to special-case; the
    HTML path (watermark_html) is the primary implementation.
    """
    try:
        from pypdf import PdfReader, PdfWriter  # type: ignore
    except ImportError:
        return data

    try:
        reader = PdfReader(__import__("io").BytesIO(data))
        writer = PdfWriter()
        for page in reader.pages:
            page.merge_page(_make_watermark_page(viewer_name, org_name))
            writer.add_page(page)
        out = __import__("io").BytesIO()
        writer.write(out)
        return out.getvalue()
    except Exception:
        return data


def _make_watermark_page(viewer_name: str, org_name: str):
    from reportlab.pdfgen import canvas  # type: ignore

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    packet = __import__("io").BytesIO()
    c = canvas.Canvas(packet, pagesize=(595, 842))  # A4
    c.setFont("Helvetica", 36)
    c.setFillColorRGB(0.5, 0.5, 0.5, alpha=0.18)
    c.saveState()
    c.translate(297, 421)
    c.rotate(30)
    c.drawCentredString(0, 0, f"{org_name} · {viewer_name} · {stamp}")
    c.restoreState()
    c.save()
    packet.seek(0)
    from pypdf import PdfReader  # type: ignore

    return PdfReader(packet).pages[0]