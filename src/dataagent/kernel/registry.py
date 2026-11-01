"""插件注册表：能力索引 / 启停 / 版本锁定 / 依赖与兼容性校验 / 显式优先级。

G3（plan.md）：注册表 P0 必备；禁后加载覆盖；禁插件间直接互调；不做独立网关。
- 禁后加载覆盖：同一 `id@version` 重复注册拒绝；同一 id 的版本一经注册即锁定。
- 显式优先级：同能力多实现按注册时给出的 priority 裁决（数值大者优先），
  同优先级无法显式裁决，注册即拒绝。
- 禁插件间互调：插件调用只经 `invoke()`，且只拿到冻结的 PluginContext（无注册表引用）。

核心控制模块（mypy strict）。
"""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from dataagent.contracts.plugin import (
    API_VERSION,
    Plugin,
    PluginContext,
    PluginManifest,
)

_REQUIREMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<target>[a-z][a-z0-9_.:-]*?)(?P<op>>=|<=|==|!=|>|<)(?P<version>[0-9][0-9A-Za-z.+~-]*)$"
)

_OPERATORS: Final[Mapping[str, str]] = {
    ">=": ">=",
    "<=": "<=",
    "==": "==",
    "!=": "!=",
    ">": ">",
    "<": "<",
}


class RegistryError(Exception):
    """注册表操作失败基类。"""


class UnknownPluginError(RegistryError):
    """按 id 找不到已注册插件。"""


class DuplicatePluginError(RegistryError):
    """同一 `id@version` 重复注册（禁后加载覆盖，G3）。"""


class VersionLockedError(RegistryError):
    """同一插件 id 已锁定另一版本。"""


class PriorityConflictError(RegistryError):
    """同一能力出现同优先级多实现，无法显式裁决。"""


class CapabilityNotFoundError(RegistryError):
    """无任何启用的插件提供该能力。"""


class CompatibilityError(RegistryError):
    """依赖缺失或版本不满足 / 冲突插件已注册。"""


@dataclass
class Registration:
    """一条插件注册（data-model.md PluginRegistration）。"""

    plugin: Plugin
    manifest: PluginManifest
    priority: int
    enabled: bool

    @property
    def ref(self) -> str:
        return self.manifest.ref


def _parse_requirement(requirement: str) -> tuple[str, str, str]:
    match = _REQUIREMENT_RE.match(requirement.strip().lower())
    if match is None:
        raise CompatibilityError(f"无法解析兼容性约束：{requirement!r}")
    operator = _OPERATORS[match.group("op")]
    return match.group("target"), operator, match.group("version")


def _parse_version(text: str) -> tuple[int, ...]:
    core = re.split(r"[-+]", text, maxsplit=1)[0]
    parts: list[int] = []
    for chunk in core.split("."):
        digits = re.match(r"\d+", chunk)
        if digits is None:
            break
        parts.append(int(digits.group()))
    return tuple(parts) or (0,)


def _pad(version: tuple[int, ...], length: int) -> tuple[int, ...]:
    return version + (0,) * (length - len(version))


def _satisfies(actual: tuple[int, ...], operator: str, expected: tuple[int, ...]) -> bool:
    length = max(len(actual), len(expected))
    left, right = _pad(actual, length), _pad(expected, length)
    return {
        ">=": left >= right,
        "<=": left <= right,
        "==": left == right,
        "!=": left != right,
        ">": left > right,
        "<": left < right,
    }[operator]


class PluginRegistry:
    """插件注册表（P0 元数据注册表，不做网关）。"""

    def __init__(
        self,
        *,
        api_version: int = API_VERSION,
        python_version: tuple[int, int, int] | None = None,
    ) -> None:
        self._api_version = api_version
        self._python_version = (
            python_version
            if python_version is not None
            else (sys.version_info.major, sys.version_info.minor, sys.version_info.micro)
        )
        self._by_id: dict[str, Registration] = {}
        self._by_capability: dict[str, list[Registration]] = {}

    def register(
        self, plugin: Plugin, *, priority: int = 0, enable: bool | None = None
    ) -> Registration:
        """注册插件；`enable=None` 时按 manifest 默认启停（unsigned 默认禁用）。"""
        manifest = plugin.manifest
        self._check_compatibility(manifest)

        existing = self._by_id.get(manifest.id)
        if existing is not None:
            if existing.manifest.version == manifest.version:
                raise DuplicatePluginError(f"{manifest.ref} 已注册；禁后加载覆盖（G3）")
            raise VersionLockedError(
                f"{manifest.id} 已锁定版本 {existing.manifest.version}，拒绝 {manifest.version}"
            )

        for signature in manifest.provides:
            for registered in self._by_capability.get(signature.capability, ()):
                if registered.priority == priority:
                    raise PriorityConflictError(
                        f"能力 {signature.capability} 已有同优先级实现 {registered.ref}"
                        f"（priority={priority}），请显式指定优先级"
                    )

        registration = Registration(
            plugin=plugin,
            manifest=manifest,
            priority=priority,
            enabled=manifest.enabled_by_default if enable is None else enable,
        )
        self._by_id[manifest.id] = registration
        for signature in manifest.provides:
            self._by_capability.setdefault(signature.capability, []).append(registration)
        return registration

    def enable(self, plugin_id: str) -> None:
        self._get(plugin_id).enabled = True

    def disable(self, plugin_id: str) -> None:
        self._get(plugin_id).enabled = False

    def resolve(self, capability: str) -> Registration:
        """按能力索引选择启用的最高优先级实现。"""
        candidates = [
            registration
            for registration in self._by_capability.get(capability, ())
            if registration.enabled
        ]
        if not candidates:
            raise CapabilityNotFoundError(f"无已启用插件提供能力 {capability}")
        return max(candidates, key=lambda registration: registration.priority)

    def invoke(
        self, capability: str, payload: Mapping[str, Any], context: PluginContext
    ) -> Any:
        """唯一插件调用入口（禁插件间直接互调）。"""
        registration = self.resolve(capability)
        return registration.plugin.invoke(capability, payload, context)

    def cacheable(self, capability: str) -> bool:
        """该能力当前命中实现是否允许结果缓存（仅 deterministic）。"""
        return self.resolve(capability).manifest.cacheable

    def _get(self, plugin_id: str) -> Registration:
        registration = self._by_id.get(plugin_id)
        if registration is None:
            raise UnknownPluginError(f"插件未注册：{plugin_id}")
        return registration

    def _check_compatibility(self, manifest: PluginManifest) -> None:
        for requirement in manifest.compatibility.requires:
            target, operator, version = _parse_requirement(requirement)
            actual = self._resolve_target(target)
            if not _satisfies(actual, operator, _parse_version(version)):
                raise CompatibilityError(
                    f"{manifest.ref} 要求 {requirement}，当前实际版本不满足"
                )

        for conflict in manifest.compatibility.conflicts:
            conflict_id = conflict.strip().removeprefix("plugin:")
            if conflict_id == manifest.id:
                continue
            if conflict_id in self._by_id:
                raise CompatibilityError(
                    f"{manifest.ref} 与已注册插件 {conflict_id} 冲突"
                )

    def _resolve_target(self, target: str) -> tuple[int, ...]:
        if target == "python":
            return self._python_version
        if target in {"api", "dataagent-api"}:
            return (self._api_version,)
        if target.startswith("plugin:"):
            dependency = self._by_id.get(target.removeprefix("plugin:"))
            if dependency is None:
                raise CompatibilityError(f"缺少依赖插件 {target}")
            return _parse_version(dependency.manifest.version)
        raise CompatibilityError(f"未知兼容性目标 {target!r}")
