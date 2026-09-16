"""Object-storage backend dispatch tests (spec 2.07 / 1.23).

Verifies that the public storage API (store/load/delete/size/ensure_bucket)
routes to the S3/MinIO backend when ``storage_backend == 's3'`` without
changing call-site behavior, and still uses the local filesystem otherwise.
"""

import io
import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.services import document_storage as ds


class FakeS3Client:
    """Minimal in-memory boto3 s3 stand-in for behavioral tests."""

    def __init__(self):
        self.objects = {}
        self.created_buckets = set()

    def put_object(self, *, Bucket, Key, Body):
        self.objects[f"{Bucket}/{Key}"] = Body

    def get_object(self, *, Bucket, Key):
        key = f"{Bucket}/{Key}"
        if key not in self.objects:
            raise RuntimeError("NoSuchKey")
        return {"Body": io.BytesIO(self.objects[key])}

    def head_object(self, *, Bucket, Key):
        key = f"{Bucket}/{Key}"
        if key not in self.objects:
            raise RuntimeError("404")
        return {"ContentLength": len(self.objects[key])}

    def delete_object(self, *, Bucket, Key):
        self.objects.pop(f"{Bucket}/{Key}", None)

    def create_bucket(self, **kwargs):
        self.created_buckets.add(kwargs["Bucket"])


@pytest.fixture
def fake_s3():
    import botocore  # noqa: F401  (ensure botocore importable for exceptions import path)
    return FakeS3Client()


def _force_s3(monkeypatch, fake):
    monkeypatch.setattr(ds.settings, "storage_backend", "s3")
    monkeypatch.setattr(ds.settings, "s3_bucket", "test-bucket")
    monkeypatch.setattr(ds, "_s3_client", lambda: fake)


@pytest.mark.parametrize("backend", ["local", "s3"])
def test_store_roundtrip(backend, monkeypatch, fake_s3, tmp_path):
    monkeypatch.setattr(ds.settings, "storage_backend", backend)
    monkeypatch.setattr(ds.settings, "document_storage_dir", str(tmp_path))
    if backend == "s3":
        monkeypatch.setattr(ds.settings, "s3_bucket", "test-bucket")
        monkeypatch.setattr(ds, "_s3_client", lambda: fake_s3)

    ref = f"org-1/agreement-1/spec.pdf"
    stored = ds.store_blob(ref, b"hello world")
    assert stored == ref
    assert ds.load_blob(ref) == b"hello world"
    assert ds.blob_size(ref) == len(b"hello world")

    ds.delete_blob(ref)
    assert ds.blob_size(ref) is None


def test_s3_delete_missing_is_idempotent(monkeypatch, fake_s3):
    _force_s3(monkeypatch, fake_s3)
    ds.delete_blob("never/stored/key.pdf")  # must not raise


def test_s3_missing_blob_raises(monkeypatch, fake_s3):
    _force_s3(monkeypatch, fake_s3)
    with pytest.raises(ds.StorageError):
        ds.load_blob("never/stored/key.pdf")


def test_ensure_bucket_creates_only_in_s3(monkeypatch, fake_s3, tmp_path):
    # local backend -> no-op
    monkeypatch.setattr(ds.settings, "storage_backend", "local")
    monkeypatch.setattr(ds.settings, "document_storage_dir", str(tmp_path))
    ds.ensure_bucket("local-bucket")
    assert not fake_s3.created_buckets

    # s3 backend -> creates bucket
    _force_s3(monkeypatch, fake_s3)
    ds.ensure_bucket("doc-bucket")
    assert "doc-bucket" in fake_s3.created_buckets


def test_local_backend_default_and_directory(monkeypatch, tmp_path):
    """Default backend is local and stores under document_storage_dir."""
    ref = f"org-2/agreement-2/letter-{uuid.uuid4().hex}.pdf"
    ds.store_blob(ref, b"local-only")
    assert (tmp_path / "org-2" / "agreement-2").exists() is False  # not this tmp dir

    # Use tmp_path via monkeypatch and confirm the file lands there.
    monkeypatch.setattr(ds.settings, "document_storage_dir", str(tmp_path))
    ref2 = "org-3/agreement-3/clause.txt"
    ds.store_blob(ref2, b"x")
    assert (tmp_path / ref2).read_bytes() == b"x"