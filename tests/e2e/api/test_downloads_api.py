"""交付访问层 HTTP 端点（PR-4-2）：单文件 / 流式 zip / manifest。

下载根目录由注入点提供；key 一律相对输出根解析，非法 key 400、缺失 404。
zip 是按需流式生成（不落盘、无 Content-Length），不是交付格式。
"""

from __future__ import annotations

import io
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from dataagent.apps.api.server import create_server
from dataagent.apps.api.status import EmptyStatusProvider

MD_KEY = "md/ab/hash.md"
JSON_KEY = "json/ab/hash.json"
TABLES_KEY = "tables/ab/hash.tables.json"
MD_BODY = "# 标题\n\n正文\n"
JSON_BODY = '{"meta": {}, "structure": {}}\n'
TABLES_BODY = '{"tables": []}\n'


def _write(root: Path, key: str, body: bytes) -> Path:
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[Path, str]]:
    root = tmp_path / "output"
    _write(root, MD_KEY, MD_BODY.encode("utf-8"))
    _write(root, JSON_KEY, JSON_BODY.encode("utf-8"))
    _write(root, TABLES_KEY, TABLES_BODY.encode("utf-8"))
    (tmp_path / "secret.txt").write_text("outside", encoding="utf-8")

    server = create_server(
        "127.0.0.1", 0, EmptyStatusProvider(), output_dir=root
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield root, url
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def get(url: str) -> tuple[int, dict[str, str], bytes]:
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.status, dict(response.headers), response.read()


def get_error(url: str) -> tuple[int, dict[str, object]]:
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(url, timeout=5)
    return excinfo.value.code, json.loads(excinfo.value.read())


def test_downloads_single_markdown(served: tuple[Path, str]) -> None:
    _, url = served
    status, headers, body = get(f"{url}/download/{MD_KEY}")

    assert status == 200
    assert headers["Content-Type"] == "text/markdown; charset=utf-8"
    assert headers["Content-Length"] == str(len(MD_BODY.encode("utf-8")))
    assert 'filename="hash.md"' in headers["Content-Disposition"]
    assert body == MD_BODY.encode("utf-8")


def test_downloads_json_and_tables_use_json_content_type(
    served: tuple[Path, str],
) -> None:
    _, url = served
    for key, expected in ((JSON_KEY, JSON_BODY), (TABLES_KEY, TABLES_BODY)):
        _, headers, body = get(f"{url}/download/{key}")
        assert headers["Content-Type"] == "application/json; charset=utf-8"
        assert body == expected.encode("utf-8")


@pytest.mark.parametrize(
    "suffix",
    [
        "%2e%2e%2fsecret.txt",
        "..%2Fsecret.txt",
        "md%2F..%2F..%2Fsecret.txt",
        "%2Fetc%2Fpasswd",
        "md%5C..%5Csecret.txt",
        "C%3A%2FWindows%2Fwin.ini",
    ],
)
def test_download_rejects_path_traversal(
    served: tuple[Path, str], suffix: str
) -> None:
    _, url = served
    code, payload = get_error(f"{url}/download/{suffix}")
    assert code == 400
    assert payload["error"] == "invalid_key"


def test_download_missing_key_is_404(served: tuple[Path, str]) -> None:
    _, url = served
    code, payload = get_error(f"{url}/download/md/ab/missing.md")
    assert code == 404
    assert payload["error"] == "not_found"


def test_download_directory_key_is_404(served: tuple[Path, str]) -> None:
    _, url = served
    code, _ = get_error(f"{url}/download/md/ab")
    assert code == 404


def test_post_download_is_rejected(served: tuple[Path, str]) -> None:
    _, url = served
    request = urllib.request.Request(
        f"{url}/download/{MD_KEY}", data=b"x", method="POST"
    )
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(request, timeout=5)
    assert excinfo.value.code == 405


def test_zip_streams_selected_keys(served: tuple[Path, str]) -> None:
    _, url = served
    status, headers, body = get(
        f"{url}/download/zip?keys={MD_KEY},{JSON_KEY}"
    )

    assert status == 200
    assert headers["Content-Type"] == "application/zip"
    # 流式：边读边压边传，不预先知道总长
    assert "Content-Length" not in headers

    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        assert archive.namelist() == [MD_KEY, JSON_KEY]
        assert archive.read(MD_KEY) == MD_BODY.encode("utf-8")
        assert archive.read(JSON_KEY) == JSON_BODY.encode("utf-8")


def test_zip_accepts_repeated_keys_and_dedupes(
    served: tuple[Path, str],
) -> None:
    _, url = served
    query = urllib.parse.urlencode(
        [("keys", MD_KEY), ("keys", JSON_KEY), ("keys", MD_KEY)]
    )
    status, _, body = get(f"{url}/download/zip?{query}")

    assert status == 200
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        assert archive.namelist() == [MD_KEY, JSON_KEY]


def test_zip_preserves_large_file_bytes(served: tuple[Path, str], tmp_path: Path) -> None:
    root, url = served
    payload = bytes(range(256)) * 1024  # 256 KB，跨多个流式分块
    _write(root, "md/cd/large.md", payload)

    _, _, body = get(f"{url}/download/zip?keys=md/cd/large.md")
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        assert archive.read("md/cd/large.md") == payload


def test_zip_requires_keys(served: tuple[Path, str]) -> None:
    _, url = served
    code, payload = get_error(f"{url}/download/zip")
    assert code == 400
    assert payload["error"] == "missing_keys"


def test_zip_rejects_traversal_key(served: tuple[Path, str]) -> None:
    _, url = served
    key = urllib.parse.quote("../secret.txt", safe="")
    code, payload = get_error(f"{url}/download/zip?keys={key}")
    assert code == 400
    assert payload["error"] == "invalid_key"


def test_zip_missing_key_is_404_before_streaming(served: tuple[Path, str]) -> None:
    _, url = served
    code, payload = get_error(
        f"{url}/download/zip?keys={MD_KEY},md/ab/missing.md"
    )
    assert code == 404
    assert payload["error"] == "not_found"
    assert payload["key"] == "md/ab/missing.md"


def test_manifest_download(served: tuple[Path, str]) -> None:
    root, url = served
    manifest = '{"doc_id": "a"}\n{"doc_id": "b"}\n'
    (root / "manifest.jsonl").write_bytes(manifest.encode("utf-8"))

    status, headers, body = get(f"{url}/manifest")
    assert status == 200
    assert headers["Content-Type"] == "application/x-ndjson; charset=utf-8"
    assert 'filename="manifest.jsonl"' in headers["Content-Disposition"]
    assert body.decode("utf-8") == manifest


def test_manifest_missing_is_404(served: tuple[Path, str]) -> None:
    _, url = served
    code, payload = get_error(f"{url}/manifest")
    assert code == 404
    assert payload["error"] == "manifest_not_found"


def test_download_endpoints_require_output_root() -> None:
    server = create_server("127.0.0.1", 0, EmptyStatusProvider())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert get(f"{url}/health")[0] == 200
        for path in ("/download/md/ab/x.md", "/download/zip?keys=a", "/manifest"):
            code, payload = get_error(f"{url}{path}")
            assert code == 503
            assert payload["error"] == "download_not_configured"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
