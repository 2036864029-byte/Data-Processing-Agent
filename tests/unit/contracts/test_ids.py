"""内容哈希 / uuidv7 测试（T008，A2）。"""

import hashlib
import uuid

import pytest

from dataagent.contracts import ids


def test_content_hash_idempotent() -> None:
    """SHA-256 幂等：同内容同哈希。"""
    content = b"hello dataagent"
    assert ids.content_hash(content) == ids.content_hash(content)
    assert ids.content_hash(content) == hashlib.sha256(content).hexdigest()
    assert len(ids.content_hash(content)) == 64


def test_content_hash_different_content_different_hash() -> None:
    assert ids.content_hash(b"a") != ids.content_hash(b"b")


def test_content_hash_str_and_bytes_equivalent() -> None:
    assert ids.content_hash("中文内容") == ids.content_hash("中文内容".encode())


def test_uuid7_version_and_variant() -> None:
    u = ids.uuid7()
    assert isinstance(u, uuid.UUID)
    assert u.version == 7
    assert u.variant == uuid.RFC_4122


def test_uuid7_monotonic_sortable() -> None:
    """uuidv7 单调可排序：生成序 == 排序序，时间前缀非递减。"""
    generated = [ids.uuid7() for _ in range(200)]
    assert generated == sorted(generated)
    prefixes = [int(u.hex[:12], 16) for u in generated]
    assert prefixes == sorted(prefixes)


def test_uuid7_unique() -> None:
    assert len({ids.uuid7() for _ in range(100)}) == 100


def test_idempotency_key_deterministic() -> None:
    kwargs = {
        "document_hash": ids.content_hash(b"file"),
        "page_range": "1-32",
        "engine": "engine.pdf@1.0.0",
        "params_hash": ids.content_hash(b"params"),
        "rulepack_version": "1",
    }
    assert ids.idempotency_key(**kwargs) == ids.idempotency_key(**kwargs)


@pytest.mark.parametrize(
    "field",
    ["document_hash", "page_range", "engine", "params_hash", "rulepack_version"],
)
def test_idempotency_key_changes_with_component(field: str) -> None:
    """幂等键 = hash(文件) + 页范围 + 引擎 + 参数 + 规则包版本：任一分量变化则键变化。"""
    base = {
        "document_hash": ids.content_hash(b"file"),
        "page_range": "1-32",
        "engine": "engine.pdf@1.0.0",
        "params_hash": ids.content_hash(b"params"),
        "rulepack_version": "1",
    }
    altered = dict(base)
    altered[field] = "DIFFERENT" if field != "page_range" else "33-64"
    assert ids.idempotency_key(**base) != ids.idempotency_key(**altered)
