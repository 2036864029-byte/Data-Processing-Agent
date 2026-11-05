"""输出根的安全视图与内容类型（PR-4-2）。

所有下载 key 都相对输出根解析：拒绝绝对路径、`..`、反斜杠与控制字符；
解析后仍须落在输出根内。下载层只读，不触碰任务状态。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dataagent.apps.api.downloads import (
    DownloadKeyError,
    DownloadNotFound,
    OutputRoot,
    content_type_for,
)


@pytest.mark.parametrize(
    "key",
    [
        "",
        "/etc/passwd",
        "../secret.txt",
        "md/../../secret.txt",
        "md/../json/a.json",
        "md\\..\\secret.txt",
        "C:/Windows/win.ini",
        "C:secret.txt",
        "md/a:b.md",
        "md//a.md",
        "md/./a.md",
        "md/\x00a.md",
        ".",
        "..",
    ],
)
def test_rejects_unsafe_keys(tmp_path: Path, key: str) -> None:
    with pytest.raises(DownloadKeyError):
        OutputRoot(tmp_path).resolve(key)


def test_resolves_key_inside_root(tmp_path: Path) -> None:
    target = tmp_path / "md" / "ab" / "hash.md"
    target.parent.mkdir(parents=True)
    target.write_text("hello", encoding="utf-8")

    assert OutputRoot(tmp_path).resolve("md/ab/hash.md") == target.resolve()


def test_open_reads_file(tmp_path: Path) -> None:
    target = tmp_path / "json" / "ab" / "hash.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")

    path, handle = OutputRoot(tmp_path).open("json/ab/hash.json")
    with handle:
        assert path == target.resolve()
        assert handle.read() == b"{}"


def test_missing_file_raises_not_found(tmp_path: Path) -> None:
    with pytest.raises(DownloadNotFound):
        OutputRoot(tmp_path).open("md/ab/missing.md")


def test_directory_key_is_not_a_download(tmp_path: Path) -> None:
    (tmp_path / "md").mkdir()
    with pytest.raises(DownloadNotFound):
        OutputRoot(tmp_path).open("md")


def test_manifest_path_none_when_absent(tmp_path: Path) -> None:
    assert OutputRoot(tmp_path).manifest_path() is None


def test_manifest_path_present(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("{}\n", encoding="utf-8")
    assert OutputRoot(tmp_path).manifest_path() == manifest


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("md/ab/hash.md", "text/markdown; charset=utf-8"),
        ("json/ab/hash.json", "application/json; charset=utf-8"),
        ("tables/ab/hash.tables.json", "application/json; charset=utf-8"),
        ("derived/ab/hash.csv", "text/csv; charset=utf-8"),
        (
            "derived/ab/hash.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        ("md/ab/hash.bin", "application/octet-stream"),
    ],
)
def test_content_type_for(key: str, expected: str) -> None:
    assert content_type_for(key) == expected
