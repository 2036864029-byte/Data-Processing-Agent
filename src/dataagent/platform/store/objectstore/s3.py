"""S3 对象存储实现（MinIO 同协议，一个实现覆盖全部 S3 兼容后端）。

boto3.client 只允许出现在此文件（宪法 G9 grep 门禁）；
业务代码经 platform/store/objectstore/ 抽象访问，禁直接 boto3.client。
"""

from __future__ import annotations

from typing import Any

import boto3
from botocore.exceptions import ClientError

from dataagent.platform.store.objectstore.base import ObjectStore

_MISSING_CODES = {"NoSuchKey", "NoSuchBucket", "NotFound", "404"}


class S3ObjectStore(ObjectStore):
    """S3 兼容对象存储：put / get / exists / delete / list。"""

    def __init__(
        self,
        endpoint_url: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1",
        client: Any = None,
    ) -> None:
        self._client = client if client is not None else boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )
        self._bucket = bucket
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        try:
            self._client.create_bucket(Bucket=self._bucket)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists", "OperationAborted"):
                raise

    def put(self, key: str, content: bytes) -> None:
        self._client.put_object(Bucket=self._bucket, Key=key, Body=content)

    def get(self, key: str) -> bytes:
        try:
            obj = self._client.get_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if self._is_missing(exc):
                raise KeyError(f"对象不存在：{key}") from exc
            raise
        return bytes(obj["Body"].read())

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except ClientError as exc:
            if self._is_missing(exc):
                return False
            raise

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def list(self, prefix: str = "") -> list[str]:
        keys: list[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return sorted(keys)

    @staticmethod
    def _is_missing(exc: ClientError) -> bool:
        return exc.response.get("Error", {}).get("Code", "") in _MISSING_CODES
