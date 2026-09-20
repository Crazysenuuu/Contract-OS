import hashlib
import os
import uuid
from abc import ABC, abstractmethod
from typing import BinaryIO

import boto3
from botocore.exceptions import ClientError
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import get_settings_lazy
from app.models.stored_object import StoredObject

settings = get_settings_lazy()


class StorageBackend(ABC):
    @abstractmethod
    async def upload(self, file_data: bytes, key: str, content_type: str) -> None:
        pass

    @abstractmethod
    async def generate_presigned_url(self, key: str, expires_seconds: int = 3600) -> str:
        pass

    @abstractmethod
    async def delete(self, key: str) -> None:
        pass


class LocalStorageBackend(StorageBackend):
    def __init__(self):
        self.base_dir = settings.document_storage_dir
        os.makedirs(self.base_dir, exist_ok=True)

    async def upload(self, file_data: bytes, key: str, content_type: str) -> None:
        file_path = os.path.join(self.base_dir, key)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        def _write():
            with open(file_path, "wb") as f:
                f.write(file_data)
        await run_in_threadpool(_write)

    async def generate_presigned_url(self, key: str, expires_seconds: int = 3600) -> str:
        # In a real app, this would route to a local download endpoint
        return f"{settings.app_base_url}/api/v1/documents/local/{key}"

    async def delete(self, key: str) -> None:
        file_path = os.path.join(self.base_dir, key)
        if os.path.exists(file_path):
            os.remove(file_path)


class S3StorageBackend(StorageBackend):
    def __init__(self):
        self.bucket = settings.s3_bucket
        kwargs = {
            "region_name": settings.s3_region_name,
        }
        if settings.s3_endpoint_url:
            kwargs["endpoint_url"] = settings.s3_endpoint_url
        if settings.s3_access_key_id:
            kwargs["aws_access_key_id"] = settings.s3_access_key_id.get_secret_value()
        if settings.s3_secret_access_key:
            kwargs["aws_secret_access_key"] = settings.s3_secret_access_key.get_secret_value()
        
        self.s3_client = boto3.client("s3", **kwargs)

    async def upload(self, file_data: bytes, key: str, content_type: str) -> None:
        def _upload():
            self.s3_client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=file_data,
                ContentType=content_type,
            )
        await run_in_threadpool(_upload)

    async def generate_presigned_url(self, key: str, expires_seconds: int = 3600) -> str:
        def _presign():
            return self.s3_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expires_seconds,
            )
        return await run_in_threadpool(_presign)

    async def delete(self, key: str) -> None:
        def _delete():
            self.s3_client.delete_object(Bucket=self.bucket, Key=key)
        await run_in_threadpool(_delete)


def get_backend() -> StorageBackend:
    if settings.storage_backend == "s3":
        return S3StorageBackend()
    return LocalStorageBackend()


class StorageService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.backend = get_backend()
        self.backend_name = settings.storage_backend

    async def upload_document(
        self,
        agreement_id: uuid.UUID,
        file_data: bytes,
        key: str,
        content_type: str,
        uploaded_by: uuid.UUID | None = None,
    ) -> StoredObject:
        """
        Uploads a document to the configured storage backend and records it in the database.
        """
        # 1. Compute hash
        sha256_hash = hashlib.sha256(file_data).hexdigest()
        size_bytes = len(file_data)

        # 2. Upload to storage
        await self.backend.upload(file_data, key, content_type)

        # 3. Save to DB
        stored_obj = StoredObject(
            agreement_id=agreement_id,
            key=key,
            sha256_hash=sha256_hash,
            size_bytes=size_bytes,
            content_type=content_type,
            backend=self.backend_name,
            uploaded_by=uploaded_by,
        )
        self.db.add(stored_obj)
        await self.db.commit()
        await self.db.refresh(stored_obj)
        return stored_obj

    async def generate_presigned_url(self, key: str, expires_seconds: int = 3600) -> str:
        """
        Generates a pre-signed download URL for a given document key.
        """
        return await self.backend.generate_presigned_url(key, expires_seconds)

    async def delete_document(self, key: str) -> None:
        """
        Deletes a document from storage and the database, enforcing legal hold rules.
        """
        # Fetch the StoredObject
        result = await self.db.execute(select(StoredObject).where(StoredObject.key == key))
        stored_obj = result.scalars().first()

        if not stored_obj:
            raise ValueError("Document not found")

        if stored_obj.legal_hold:
            raise ValueError("Cannot delete document under legal hold")

        # Delete from backend
        await self.backend.delete(key)

        # Delete from DB
        await self.db.delete(stored_obj)
        await self.db.commit()
