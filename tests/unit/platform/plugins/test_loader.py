"""插件加载器测试（T034，A5）。

热路径同进程直接调用、冷路径子进程沙箱；manifest 目录发现与校验。
"""

from pathlib import Path

import pytest
import yaml

from dataagent.contracts.plugin import PluginContext
from dataagent.platform.plugins.loader import (
    PluginLoadError,
    discover_manifests,
    load_plugin,
    read_manifest,
)

HOT_PLUGIN_SOURCE = '''
from dataagent.contracts.plugin import PluginManifest

MANIFEST = PluginManifest.model_validate(
    {
        "id": "engine.hot",
        "name": "engine.hot",
        "version": "1.0.0",
        "api_version": 1,
        "kind": "engine",
        "provides": [
            {
                "capability": "txt.classify",
                "input": {"schema": "doc.bytes"},
                "output": {"schema": "out.v1"},
                "latency": {"p50_ms": 1, "unit": "page"},
            }
        ],
        "determinism": "deterministic",
        "trust": "builtin",
        "sandbox": "none",
    }
)


class HotPlugin:
    manifest = MANIFEST

    def invoke(self, capability, payload, context):
        return {
            "capability": capability,
            "value": int(payload["value"]) + 1,
            "doc_id": context.doc_id,
        }


PLUGIN = HotPlugin()
'''

COLD_PLUGIN_SOURCE = '''
import json
import sys

request = json.load(sys.stdin)
json.dump(
    {
        "result": {
            "capability": request["capability"],
            "value": request["payload"]["value"],
            "doc_id": request["context"]["doc_id"],
        }
    },
    sys.stdout,
)
'''


def make_manifest(
    plugin_id: str = "engine.hot",
    *,
    capability: str = "txt.classify",
    sandbox: str = "none",
) -> dict[str, object]:
    return {
        "id": plugin_id,
        "name": plugin_id,
        "version": "1.0.0",
        "api_version": 1,
        "kind": "engine",
        "provides": [
            {
                "capability": capability,
                "input": {"schema": "doc.bytes"},
                "output": {"schema": "out.v1"},
                "latency": {"p50_ms": 1, "unit": "page"},
            }
        ],
        "determinism": "deterministic",
        "trust": "builtin",
        "sandbox": sandbox,
        "limits": {"timeout_s": 20},
    }


def write_plugin(
    root: Path,
    name: str,
    manifest: dict[str, object],
    source: str | None,
) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "manifest.yaml").write_text(
        yaml.safe_dump({"manifest": manifest}, allow_unicode=True), encoding="utf-8"
    )
    if source is not None:
        (directory / "plugin.py").write_text(source, encoding="utf-8")
    return directory


def test_read_manifest_unwraps_top_level_key(tmp_path: Path) -> None:
    directory = write_plugin(tmp_path, "hot", make_manifest(), None)
    manifest = read_manifest(directory / "manifest.yaml")
    assert manifest.id == "engine.hot"
    assert manifest.ref == "engine.hot@1.0.0"


def test_discover_manifests_skips_dirs_without_manifest(tmp_path: Path) -> None:
    write_plugin(tmp_path, "b", make_manifest(plugin_id="engine.b"), None)
    write_plugin(tmp_path, "a", make_manifest(plugin_id="engine.a"), None)
    (tmp_path / "junk").mkdir()

    manifests = discover_manifests(tmp_path)
    assert [manifest.id for manifest in manifests] == ["engine.a", "engine.b"]


def test_load_hot_plugin_invokes_in_process(tmp_path: Path) -> None:
    directory = write_plugin(
        tmp_path,
        "hot",
        make_manifest(capability="txt.classify", sandbox="none"),
        HOT_PLUGIN_SOURCE,
    )
    plugin = load_plugin(directory)

    result = plugin.invoke(
        "txt.classify", {"value": 1}, PluginContext(task_id="t1", doc_id="d1")
    )
    assert result == {"capability": "txt.classify", "value": 2, "doc_id": "d1"}


def test_load_cold_plugin_runs_in_subprocess(tmp_path: Path) -> None:
    directory = write_plugin(
        tmp_path,
        "cold",
        make_manifest(plugin_id="engine.cold", capability="txt.text.extract", sandbox="subprocess"),
        COLD_PLUGIN_SOURCE,
    )
    plugin = load_plugin(directory)

    result = plugin.invoke(
        "txt.text.extract", {"value": 7}, PluginContext(task_id="t1", doc_id="d2")
    )
    assert result == {"capability": "txt.text.extract", "value": 7, "doc_id": "d2"}


def test_missing_manifest_rejected(tmp_path: Path) -> None:
    directory = tmp_path / "empty"
    directory.mkdir()
    with pytest.raises(PluginLoadError):
        load_plugin(directory)


def test_hot_plugin_missing_module_rejected(tmp_path: Path) -> None:
    directory = write_plugin(tmp_path, "hot", make_manifest(), None)
    with pytest.raises(PluginLoadError):
        load_plugin(directory)


def test_hot_plugin_manifest_mismatch_rejected(tmp_path: Path) -> None:
    directory = write_plugin(
        tmp_path, "hot", make_manifest(plugin_id="engine.hot"), HOT_PLUGIN_SOURCE
    )
    # 磁盘 manifest 与插件内 manifest 不一致
    (directory / "manifest.yaml").write_text(
        yaml.safe_dump({"manifest": make_manifest(plugin_id="engine.other")}),
        encoding="utf-8",
    )
    with pytest.raises(PluginLoadError):
        load_plugin(directory)


def test_container_sandbox_unsupported_in_p0(tmp_path: Path) -> None:
    directory = write_plugin(
        tmp_path,
        "container",
        make_manifest(sandbox="container"),
        COLD_PLUGIN_SOURCE,
    )
    with pytest.raises(PluginLoadError):
        load_plugin(directory)
