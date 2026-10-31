"""对象存储抽象接口 + 内容寻址 key 约定。

宪法 / data-model 主键铁律：文档与页产物主键必须是内容哈希（SHA-256），换底座 key 不变。
业务代码只经 platform/store/objectstore/ 访问对象存储，禁直接 boto3.client（G9 门禁）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from dataagent.contracts.ids import content_hash


def content_key(content: bytes | str) -> str:
    """内容寻址 key：主键 = SHA-256 内容哈希（同内容同 key，换底座不变）。"""
    return content_hash(content)


class ObjectStore(ABC):
    """对象存储最小抽象：put / get / exists / delete / list。"""

    @abstractmethod
    def put(self, key: str, content: bytes) -> None:
        """写入对象；已存在同 key 时覆盖（内容寻址下同 key 必同内容，幂等）。"""

    @abstractmethod
    def get(self, key: str) -> bytes:
        """读取对象；不存在抛 KeyError。"""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """对象是否存在。"""

    @abstractmethod
    def delete(self, key: str) -> None:
        """删除对象（幂等：不存在不抛错）。"""

    @abstractmethod
    def list(self, prefix: str = "") -> list[str]:
        """列出匹配前缀的 key（按字典序）。"""
