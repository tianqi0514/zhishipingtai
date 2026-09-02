from __future__ import annotations

import shutil
import io
from pathlib import Path
from collections.abc import Iterator

from minio import Minio
from minio.error import S3Error

from .config import get_settings


class ObjectStorage:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._client: Minio | None = None

    @property
    def client(self) -> Minio:
        if self._client is None:
            self._client = Minio(
                self.settings.object_store_endpoint,
                access_key=self.settings.object_store_access_key,
                secret_key=self.settings.object_store_secret_key,
                secure=self.settings.object_store_secure,
            )
        return self._client

    def ensure_bucket(self) -> None:
        if self.settings.use_local_object_store:
            self.settings.local_storage_path.mkdir(parents=True, exist_ok=True)
            return
        if not self.client.bucket_exists(self.settings.object_store_bucket):
            self.client.make_bucket(self.settings.object_store_bucket)

    def put_file(self, object_key: str, path: Path, content_type: str) -> None:
        self.ensure_bucket()
        if self.settings.use_local_object_store:
            target = self.settings.local_storage_path / object_key
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            return
        self.client.fput_object(
            self.settings.object_store_bucket,
            object_key,
            str(path),
            content_type=content_type or "application/octet-stream",
        )

    def get_file(self, object_key: str, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        if self.settings.use_local_object_store:
            shutil.copyfile(self.settings.local_storage_path / object_key, target)
            return
        self.client.fget_object(self.settings.object_store_bucket, object_key, str(target))

    def get_bytes(self, object_key: str) -> bytes:
        if self.settings.use_local_object_store:
            return (self.settings.local_storage_path / object_key).read_bytes()
        response = self.client.get_object(self.settings.object_store_bucket, object_key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def stat_size(self, object_key: str) -> int:
        """Return object size without downloading the object body."""
        if self.settings.use_local_object_store:
            return (self.settings.local_storage_path / object_key).stat().st_size
        return int(self.client.stat_object(self.settings.object_store_bucket, object_key).size)

    def iter_bytes(
        self,
        object_key: str,
        *,
        offset: int = 0,
        length: int | None = None,
        chunk_size: int = 1024 * 1024,
    ) -> Iterator[bytes]:
        """Stream a bounded object range without buffering a large media file."""
        if offset < 0 or (length is not None and length < 0):
            raise ValueError("offset 和 length 不能为负数")
        remaining = length
        if self.settings.use_local_object_store:
            with (self.settings.local_storage_path / object_key).open("rb") as stream:
                stream.seek(offset)
                while remaining is None or remaining > 0:
                    size = chunk_size if remaining is None else min(chunk_size, remaining)
                    block = stream.read(size)
                    if not block:
                        break
                    if remaining is not None:
                        remaining -= len(block)
                    yield block
            return
        response = self.client.get_object(
            self.settings.object_store_bucket,
            object_key,
            offset=offset,
            length=length,
        )
        try:
            while remaining is None or remaining > 0:
                size = chunk_size if remaining is None else min(chunk_size, remaining)
                block = response.read(size)
                if not block:
                    break
                if remaining is not None:
                    remaining -= len(block)
                yield block
        finally:
            response.close()
            response.release_conn()

    def delete(self, object_key: str) -> None:
        if self.settings.use_local_object_store:
            path = self.settings.local_storage_path / object_key
            if path.exists():
                path.unlink()
            return
        try:
            self.client.remove_object(self.settings.object_store_bucket, object_key)
        except S3Error:
            raise

    def presigned_get(self, object_key: str, expires_seconds: int = 900) -> str | None:
        if self.settings.use_local_object_store:
            return None
        from datetime import timedelta

        return self.client.presigned_get_object(
            self.settings.object_store_bucket,
            object_key,
            expires=timedelta(seconds=expires_seconds),
        )


object_storage = ObjectStorage()
