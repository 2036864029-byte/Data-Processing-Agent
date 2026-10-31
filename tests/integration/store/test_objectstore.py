"""对象存储契约测试（T022，A4）。

put / get / exists / delete / list；内容哈希 key 换底座不变；
同一套套件参数化分别跑 local 与 MinIO（宪法：业务只经 platform/store/objectstore/，
禁直接 boto3.client —— boto3.client 仅允许出现在 s3.py）。
"""

import uuid

import pytest

from dataagent.contracts.ids import content_hash
from dataagent.platform.store.objectstore.base import content_key
from dataagent.platform.store.objectstore.local import LocalObjectStore
from dataagent.platform.store.objectstore.s3 import S3ObjectStore

pytestmark = pytest.mark.integration


def _s3_from_minio(minio, bucket: str) -> S3ObjectStore:
    host = minio.get_container_host_ip()
    port = minio.get_exposed_port(minio.port)
    return S3ObjectStore(
        endpoint_url=f"http://{host}:{port}",
        bucket=bucket,
        access_key=minio.access_key,
        secret_key=minio.secret_key,
    )


@pytest.fixture(params=["local", "s3"])
def object_store(request, tmp_path):
    """同一套对象存储测试套件参数化跑 local 与 MinIO（s3 参数才拉起 MinIO 容器）。

    local 用独立临时目录、s3 用每测试唯一 bucket，保证各用例互不污染。
    """
    if request.param == "local":
        return LocalObjectStore(tmp_path / "objects")
    minio = request.getfixturevalue("minio_container")
    return _s3_from_minio(minio, bucket=f"dataagent-test-{uuid.uuid4().hex[:8]}")


def test_content_key_is_sha256_content_hash() -> None:
    """内容寻址 key 约定：主键 = SHA-256 内容哈希（data-model 主键铁律）。"""
    content = b"deterministic content"
    assert content_key(content) == content_hash(content)
    assert len(content_key(content)) == 64


def test_put_get_roundtrip(object_store) -> None:
    key = content_key(b"hello dataagent")
    object_store.put(key, b"hello dataagent")
    assert object_store.get(key) == b"hello dataagent"


def test_get_missing_raises_keyerror(object_store) -> None:
    with pytest.raises(KeyError):
        object_store.get(content_key(b"not-present"))


def test_exists(object_store) -> None:
    key = content_key(b"exists-blob")
    assert not object_store.exists(key)
    object_store.put(key, b"exists-blob")
    assert object_store.exists(key)


def test_delete_removes_object(object_store) -> None:
    key = content_key(b"to-delete")
    object_store.put(key, b"to-delete")
    assert object_store.exists(key)
    object_store.delete(key)
    assert not object_store.exists(key)
    with pytest.raises(KeyError):
        object_store.get(key)


def test_delete_missing_is_noop(object_store) -> None:
    object_store.delete(content_key(b"never-there"))  # 幂等，不抛错


def test_put_idempotent_same_content(object_store) -> None:
    """同一内容重复 put（内容寻址幂等）后仍可读回原内容。"""
    key = content_key(b"idempotent")
    object_store.put(key, b"idempotent")
    object_store.put(key, b"idempotent")
    assert object_store.get(key) == b"idempotent"


def test_list_and_prefix(object_store) -> None:
    keys = [content_key(f"page-{i}".encode()) for i in range(3)]
    for k in keys:
        object_store.put(k, b"x")
    listed = object_store.list()
    assert set(listed) == set(keys)
    assert listed == sorted(listed)
    # prefix 过滤（内容哈希前缀可定向查询）
    one = object_store.list(prefix=keys[0][:12])
    assert one == [keys[0]]


def test_content_key_stable_across_backends(object_store) -> None:
    """内容哈希 key 换底座不变：同一内容在 local 与 S3 下得到同一 key。"""
    content = b"stable-across-backends"
    assert content_key(content) == content_hash(content)
    object_store.put(content_key(content), content)
    assert object_store.get(content_key(content)) == content


def test_bucket_reused_across_store_instances(minio_container) -> None:
    """同一 bucket 被两个 S3ObjectStore 实例复用（create_bucket 已存在不报错）。"""
    bucket = f"dataagent-test-reuse-{uuid.uuid4().hex[:8]}"
    store1 = _s3_from_minio(minio_container, bucket)
    store2 = _s3_from_minio(minio_container, bucket)
    key = content_key(b"reuse")
    store1.put(key, b"reuse")
    assert store2.get(key) == b"reuse"
