"""本地盘对象存储单元测试（T026 补充）。

非法 key 拒绝、原子写失败后临时文件清理（先写内容再写索引的一致性保障）。
"""

import pytest

from dataagent.platform.store.objectstore.base import content_key
from dataagent.platform.store.objectstore.local import LocalObjectStore


def test_put_invalid_key_rejected(tmp_path) -> None:
    """非法 key（含路径分隔符 / 空串）拒绝，防路径穿越。"""
    store = LocalObjectStore(tmp_path / "obj")
    with pytest.raises(ValueError):
        store.put("a/b", b"x")
    with pytest.raises(ValueError):
        store.put("", b"x")
    with pytest.raises(ValueError):
        store.put(".hidden", b"x")


def test_put_cleans_temp_file_on_replace_failure(tmp_path, monkeypatch) -> None:
    """原子写失败（os.replace 抛错）后临时文件被清理，不留半截文件。"""
    store = LocalObjectStore(tmp_path / "obj")

    def boom(src, dst):
        raise OSError("disk error")

    monkeypatch.setattr("dataagent.platform.store.objectstore.local.os.replace", boom)
    with pytest.raises(OSError):
        store.put(content_key(b"x"), b"x")
    assert list((tmp_path / "obj" / ".tmp").iterdir()) == []
