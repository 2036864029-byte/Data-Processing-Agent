"""内容哈希（SHA-256）与 uuidv7。

主键铁律（data-model.md）：文档与页产物的主键必须是内容哈希，不得使用自增 ID。
"""

import hashlib
import secrets
import time
from uuid import UUID


def content_hash(content: bytes | str) -> str:
    """SHA-256 内容哈希（64 位十六进制）。同内容同哈希、不同内容不同哈希。"""
    raw = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(raw).hexdigest()


_uuid7_last_ms: int | None = None
_uuid7_counter: int = 0


def uuid7() -> UUID:
    """UUIDv7：48 位毫秒时间戳前缀 + 版本 7 + 单调计数器，生成序即排序序。

    Python 3.12 无内置 uuid7，自实现（layout 遵循 RFC 9562）。
    """
    global _uuid7_last_ms, _uuid7_counter
    now_ms = int(time.time() * 1000)
    if now_ms != _uuid7_last_ms:
        _uuid7_last_ms = now_ms
        _uuid7_counter = 0
    else:
        _uuid7_counter += 1
    rand_a = _uuid7_counter & 0xFFF
    rand_b = secrets.randbits(62)
    value = (now_ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    # Python 3.12 的 UUID 构造仅接受 version 1–5，故不传 version 参数；
    # UUID.version / UUID.variant 属性会按位识别 version 7 与 RFC 4122。
    return UUID(int=value)


def idempotency_key(
    document_hash: str,
    page_range: str,
    engine: str,
    params_hash: str,
    rulepack_version: str,
) -> str:
    """幂等键 = hash(文件) + 页范围 + 引擎 + 参数 + 规则包版本（data-model.md PageArtifact 规则）。

    同一幂等键必须返回同一 artifact（内容寻址）。
    """
    return f"{document_hash}:{page_range}:{engine}:{params_hash}:{rulepack_version}"


def result_cache_key(document_hash: str, params_hash: str, engine_ref: str) -> str:
    """结果缓存键 = 内容哈希 + 参数哈希 + `id@version`（plugin-manifest.md 关键语义）。

    仅 `determinism: deterministic` 的插件允许使用该键缓存结果。
    """
    return f"{document_hash}:{params_hash}:{engine_ref}"
