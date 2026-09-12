from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO, Protocol

import boto3
from botocore.exceptions import ClientError


class StorageBackend(Protocol):
    def put(self, key: str, source: BinaryIO, content_type: str) -> None: ...
    def delete(self, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...
    def open(self, key: str) -> BinaryIO: ...
    def size(self, key: str) -> int: ...
    def open_range(self, key: str, start: int, end: int) -> BinaryIO: ...


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / key

    def put(self, key: str, source: BinaryIO, content_type: str) -> None:
        destination = self._path(key)
        with destination.open("xb") as output:
            source.seek(0)
            while chunk := source.read(1024 * 1024):
                output.write(chunk)

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def open(self, key: str) -> BinaryIO:
        return self._path(key).open("rb")

    def size(self, key: str) -> int:
        return self._path(key).stat().st_size

    def open_range(self, key: str, start: int, end: int) -> BinaryIO:
        source = self._path(key).open("rb")
        source.seek(start)
        return source


class S3Storage:
    def __init__(self, bucket: str, prefix: str = ""):
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = boto3.client(
                "s3",
                endpoint_url=os.getenv("S3_ENDPOINT_URL") or None,
                region_name=os.getenv("AWS_REGION") or None,
            )
        return self._client

    def _key(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def put(self, key: str, source: BinaryIO, content_type: str) -> None:
        source.seek(0)
        self.client.upload_fileobj(
            source,
            self.bucket,
            self._key(key),
            ExtraArgs={"ContentType": content_type},
        )

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._key(key))

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._key(key))
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in {"404", "NoSuchKey"}:
                return False
            raise
        return True

    def open(self, key: str) -> BinaryIO:
        response = self.client.get_object(Bucket=self.bucket, Key=self._key(key))
        return response["Body"]

    def size(self, key: str) -> int:
        response = self.client.head_object(Bucket=self.bucket, Key=self._key(key))
        return int(response["ContentLength"])

    def open_range(self, key: str, start: int, end: int) -> BinaryIO:
        response = self.client.get_object(
            Bucket=self.bucket,
            Key=self._key(key),
            Range=f"bytes={start}-{end}",
        )
        return response["Body"]


def create_storage(root: Path) -> StorageBackend:
    bucket = os.getenv("S3_BUCKET")
    if bucket:
        return S3Storage(bucket, os.getenv("S3_PREFIX", ""))
    return LocalStorage(root)
