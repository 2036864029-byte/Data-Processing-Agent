"""S3 对象存储错误分支单元测试（T027 补充）。

用注入的假 client 覆盖不易在真实 MinIO 上触发的错误传播分支
（bucket 已存在 / 非缺失错误原样抛出），保证核心模块覆盖率门禁。
"""

import pytest
from botocore.exceptions import ClientError

from dataagent.platform.store.objectstore.s3 import S3ObjectStore


def _client_error(code: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": 400}},
        "TestOperation",
    )


class FakeS3Client:
    """可注入逐方法错误的假 S3 client。"""

    def __init__(
        self,
        create_bucket_error: ClientError | None = None,
        get_object_error: ClientError | None = None,
        head_object_error: ClientError | None = None,
    ) -> None:
        self._create_bucket_error = create_bucket_error
        self._get_object_error = get_object_error
        self._head_object_error = head_object_error

    def create_bucket(self, Bucket: str) -> None:
        if self._create_bucket_error is not None:
            raise self._create_bucket_error

    def get_object(self, Bucket: str, Key: str) -> None:
        if self._get_object_error is not None:
            raise self._get_object_error
        raise AssertionError("unexpected get_object")

    def head_object(self, Bucket: str, Key: str) -> None:
        if self._head_object_error is not None:
            raise self._head_object_error
        raise AssertionError("unexpected head_object")


def _store(client) -> S3ObjectStore:
    return S3ObjectStore(
        endpoint_url="http://unused", bucket="b", access_key="a", secret_key="s", client=client
    )


def test_ensure_bucket_accepts_already_owned() -> None:
    """bucket 已存在（BucketAlreadyOwnedByYou）：复用不报错。"""
    _store(FakeS3Client(create_bucket_error=_client_error("BucketAlreadyOwnedByYou")))


def test_ensure_bucket_re_raises_other_error() -> None:
    """非「已存在」类错误原样抛出。"""
    with pytest.raises(ClientError):
        _store(FakeS3Client(create_bucket_error=_client_error("AccessDenied")))


def test_get_re_raises_non_missing_error() -> None:
    """get 的非缺失错误原样抛出（不误转 KeyError）。"""
    store = _store(FakeS3Client(get_object_error=_client_error("InternalError")))
    with pytest.raises(ClientError):
        store.get("k")


def test_exists_re_raises_non_missing_error() -> None:
    """exists 的非缺失错误原样抛出（不误判为不存在）。"""
    store = _store(FakeS3Client(head_object_error=_client_error("InternalError")))
    with pytest.raises(ClientError):
        store.exists("k")
