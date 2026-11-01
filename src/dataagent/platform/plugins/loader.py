"""插件加载器：从插件目录读取 manifest 并加载插件。

- 热路径插件（`sandbox: none`）：同进程直接 import `plugin.py`，取模块级 `PLUGIN`，
  其 `manifest` 必须与 `manifest.yaml` 的 id/version 一致。
- 冷路径插件（`sandbox: subprocess`）：不在本进程 import；`invoke` 经子进程沙箱执行
  `plugin.py`，请求 / 响应均为 JSON（UTF-8）：
  请求 `{"capability", "payload", "context": {"task_id", "doc_id"}}`；
  响应为任意 JSON，若为对象且含 `result` 键则返回该键值，否则原样返回。
- `sandbox: container / remote`：契约允许声明，P0 不实现执行，加载即拒绝。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml

from dataagent.contracts.plugin import (
    Plugin,
    PluginContext,
    PluginManifest,
    SandboxMode,
    manifest_from_document,
)
from dataagent.platform.runtime.sandbox import SandboxLimits, SubprocessSandbox

MANIFEST_FILENAMES: tuple[str, ...] = ("manifest.yaml", "manifest.yml")
PLUGIN_MODULE_FILENAME = "plugin.py"


class PluginLoadError(Exception):
    """插件目录不合法或插件无法加载。"""


class PluginExecutionError(PluginLoadError):
    """冷路径插件执行失败（超时 / 非零退出 / 响应不可解析）。"""


def discover_manifests(plugin_root: str | Path) -> list[PluginManifest]:
    """扫描插件根目录，返回各子目录的 manifest（无 manifest 的子目录跳过）。"""
    root = Path(plugin_root)
    if not root.is_dir():
        raise PluginLoadError(f"插件目录不存在：{root}")
    manifests: list[PluginManifest] = []
    for directory in sorted(path for path in root.iterdir() if path.is_dir()):
        manifest_path = _find_manifest(directory)
        if manifest_path is not None:
            manifests.append(read_manifest(manifest_path))
    return manifests


def read_manifest(path: str | Path) -> PluginManifest:
    """读取并校验 manifest 文件（兼容顶层 `manifest:` 包裹）。"""
    manifest_path = Path(path)
    try:
        document = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PluginLoadError(f"无法读取 manifest {manifest_path}: {exc}") from exc
    if not isinstance(document, Mapping):
        raise PluginLoadError(f"manifest 必须是映射：{manifest_path}")
    try:
        return manifest_from_document(document)
    except ValueError as exc:
        raise PluginLoadError(f"manifest 校验失败 {manifest_path}: {exc}") from exc


def load_plugin(
    plugin_dir: str | Path, *, sandbox: SubprocessSandbox | None = None
) -> Plugin:
    """按 manifest 声明的 sandbox 模式加载插件。"""
    directory = Path(plugin_dir)
    manifest_path = _find_manifest(directory)
    if manifest_path is None:
        raise PluginLoadError(f"插件目录缺少 manifest：{directory}")
    manifest = read_manifest(manifest_path)

    if manifest.sandbox is SandboxMode.NONE:
        return _load_in_process(directory, manifest)
    if manifest.sandbox is SandboxMode.SUBPROCESS:
        _require_plugin_module(directory)
        return SandboxedPlugin(
            directory=directory,
            manifest=manifest,
            sandbox=sandbox if sandbox is not None else SubprocessSandbox(),
        )
    raise PluginLoadError(f"P0 未实现 sandbox={manifest.sandbox.value}：{directory}")


@dataclass(frozen=True, slots=True)
class SandboxedPlugin:
    """冷路径插件代理：每次 invoke 都在子进程中执行 `plugin.py`。"""

    directory: Path
    manifest: PluginManifest
    sandbox: SubprocessSandbox

    def invoke(
        self, capability: str, payload: Mapping[str, Any], context: PluginContext
    ) -> Any:
        request = {
            "capability": capability,
            "payload": payload,
            "context": {"task_id": context.task_id, "doc_id": context.doc_id},
        }
        try:
            stdin = json.dumps(request).encode("utf-8")
        except TypeError as exc:
            raise PluginExecutionError(
                f"插件 {self.manifest.ref} 载荷必须可 JSON 序列化：{exc}"
            ) from exc

        limits = SandboxLimits(timeout_s=self.manifest.limits.timeout_s)
        result = self.sandbox.run(
            [sys.executable, str(self.directory / PLUGIN_MODULE_FILENAME)],
            limits=limits,
            cwd=self.directory,
            stdin=stdin,
        )
        if result.timed_out:
            raise PluginExecutionError(
                f"插件 {self.manifest.ref} 执行超时（{limits.timeout_s}s）"
            )
        if not result.ok:
            raise PluginExecutionError(
                f"插件 {self.manifest.ref} 退出码 {result.returncode}："
                f"{result.stderr.decode('utf-8', 'replace')}"
            )
        try:
            document = json.loads(result.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PluginExecutionError(
                f"插件 {self.manifest.ref} 响应不是合法 JSON：{exc}"
            ) from exc
        if isinstance(document, dict) and "result" in document:
            return document["result"]
        return document


def _find_manifest(directory: Path) -> Path | None:
    for name in MANIFEST_FILENAMES:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def _require_plugin_module(directory: Path) -> None:
    if not (directory / PLUGIN_MODULE_FILENAME).is_file():
        raise PluginLoadError(f"插件目录缺少 {PLUGIN_MODULE_FILENAME}：{directory}")


def _load_in_process(directory: Path, manifest: PluginManifest) -> Plugin:
    _require_plugin_module(directory)
    module_path = directory / PLUGIN_MODULE_FILENAME
    module_name = (
        f"_dataagent_plugin_{manifest.id.replace('.', '_')}_"
        f"{manifest.version.replace('.', '_')}"
    )
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise PluginLoadError(f"无法为插件模块建立加载 spec：{module_path}")

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise PluginLoadError(f"插件模块导入失败 {module_path}: {exc}") from exc

    candidate = getattr(module, "PLUGIN", None)
    if candidate is None:
        raise PluginLoadError(f"{PLUGIN_MODULE_FILENAME} 必须暴露模块级 PLUGIN：{module_path}")
    if not callable(getattr(candidate, "invoke", None)):
        raise PluginLoadError(f"PLUGIN 必须实现 invoke()：{module_path}")

    loaded_manifest = getattr(candidate, "manifest", None)
    if not isinstance(loaded_manifest, PluginManifest):
        raise PluginLoadError(f"PLUGIN.manifest 必须是 PluginManifest：{module_path}")
    if (
        loaded_manifest.id != manifest.id
        or loaded_manifest.version != manifest.version
    ):
        raise PluginLoadError(
            f"PLUGIN.manifest 与 manifest.yaml 不一致："
            f"{loaded_manifest.ref} != {manifest.ref}"
        )
    return cast(Plugin, candidate)
