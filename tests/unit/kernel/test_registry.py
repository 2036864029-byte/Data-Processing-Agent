"""插件注册表行为测试（T032，A5）。

能力索引 / 启停 / 版本锁定 / 依赖兼容校验 / 同能力多实现显式优先级 /
后加载覆盖被拒（G3）；determinism 非 deterministic 禁启用结果缓存；
插件只拿到冻结的 PluginContext，拿不到注册表（禁插件间互调）。
"""

from collections.abc import Mapping
from dataclasses import FrozenInstanceError, dataclass, field
from typing import Any

import pytest

from dataagent.contracts.plugin import PluginContext, PluginManifest
from dataagent.kernel.registry import (
    CapabilityNotFoundError,
    CompatibilityError,
    DuplicatePluginError,
    PluginRegistry,
    PriorityConflictError,
    UnknownPluginError,
    VersionLockedError,
)


def make_manifest(
    plugin_id: str = "engine.fake",
    *,
    version: str = "1.0.0",
    capabilities: tuple[str, ...] = ("pdf.classify",),
    determinism: str = "deterministic",
    trust: str = "builtin",
    sandbox: str = "none",
    requires: tuple[str, ...] = (),
    conflicts: tuple[str, ...] = (),
) -> PluginManifest:
    return PluginManifest.model_validate(
        {
            "id": plugin_id,
            "name": plugin_id,
            "version": version,
            "api_version": 1,
            "kind": "engine",
            "provides": [
                {
                    "capability": capability,
                    "input": {"schema": "doc.bytes"},
                    "output": {"schema": "out.v1"},
                    "latency": {"p50_ms": 1, "unit": "page"},
                }
                for capability in capabilities
            ],
            "determinism": determinism,
            "trust": trust,
            "sandbox": sandbox,
            "compatibility": {"requires": list(requires), "conflicts": list(conflicts)},
        }
    )


@dataclass
class FakePlugin:
    manifest: PluginManifest
    calls: list[tuple[str, Mapping[str, Any], PluginContext]] = field(default_factory=list)

    def invoke(
        self, capability: str, payload: Mapping[str, Any], context: PluginContext
    ) -> dict[str, Any]:
        self.calls.append((capability, payload, context))
        return {"capability": capability, "value": payload.get("value"), "doc_id": context.doc_id}


def test_capability_index_resolves_each_provided_capability() -> None:
    """能力索引：manifest 每个 provides 均可命中同一插件。"""
    plugin = FakePlugin(make_manifest(capabilities=("pdf.classify", "pdf.text.extract")))
    registry = PluginRegistry()
    registry.register(plugin)

    assert registry.resolve("pdf.classify").plugin is plugin
    assert registry.resolve("pdf.text.extract").plugin is plugin
    with pytest.raises(CapabilityNotFoundError):
        registry.resolve("pdf.ocr")


def test_register_same_version_twice_rejected() -> None:
    """后加载覆盖被拒（G3）：同一 id@version 不得重复注册。"""
    registry = PluginRegistry()
    registry.register(FakePlugin(make_manifest()))
    with pytest.raises(DuplicatePluginError):
        registry.register(FakePlugin(make_manifest()))


def test_version_locked_after_first_registration() -> None:
    """版本锁定：同一插件 id 只能锁定一个版本。"""
    registry = PluginRegistry()
    registry.register(FakePlugin(make_manifest(version="1.0.0")))
    with pytest.raises(VersionLockedError):
        registry.register(FakePlugin(make_manifest(version="2.0.0")))


def test_explicit_priority_selects_implementation() -> None:
    """同能力多实现按显式优先级选择（数值大者优先）。"""
    low = FakePlugin(make_manifest(plugin_id="engine.low"))
    high = FakePlugin(make_manifest(plugin_id="engine.high"))
    registry = PluginRegistry()
    registry.register(low, priority=0)
    registry.register(high, priority=10)

    assert registry.resolve("pdf.classify").plugin is high


def test_equal_priority_collision_rejected() -> None:
    """同能力同优先级无法显式裁决，注册即拒绝。"""
    registry = PluginRegistry()
    registry.register(FakePlugin(make_manifest(plugin_id="engine.one")), priority=5)
    with pytest.raises(PriorityConflictError):
        registry.register(FakePlugin(make_manifest(plugin_id="engine.two")), priority=5)


def test_disable_falls_back_then_enable_restores() -> None:
    """启停：禁用高优先级实现后回退到次优先；全部禁用则不可解析。"""
    low = FakePlugin(make_manifest(plugin_id="engine.low"))
    high = FakePlugin(make_manifest(plugin_id="engine.high"))
    registry = PluginRegistry()
    registry.register(low, priority=0)
    registry.register(high, priority=10)

    registry.disable("engine.high")
    assert registry.resolve("pdf.classify").plugin is low

    registry.disable("engine.low")
    with pytest.raises(CapabilityNotFoundError):
        registry.resolve("pdf.classify")

    registry.enable("engine.low")
    assert registry.resolve("pdf.classify").plugin is low


def test_enable_or_disable_unknown_plugin_rejected() -> None:
    registry = PluginRegistry()
    with pytest.raises(UnknownPluginError):
        registry.enable("engine.ghost")
    with pytest.raises(UnknownPluginError):
        registry.disable("engine.ghost")


def test_unsigned_plugin_disabled_by_default_until_explicitly_enabled() -> None:
    """trust: unsigned 默认禁用，需显式 enable（验收 4）。"""
    plugin = FakePlugin(make_manifest(trust="unsigned", sandbox="subprocess"))
    registry = PluginRegistry()
    registration = registry.register(plugin)
    assert registration.enabled is False
    with pytest.raises(CapabilityNotFoundError):
        registry.resolve("pdf.classify")

    registry.enable("engine.fake")
    assert registry.resolve("pdf.classify").plugin is plugin


def test_compatibility_python_and_api_constraints() -> None:
    """依赖兼容校验：python / api 版本约束在注册时校验。"""
    registry = PluginRegistry(api_version=1, python_version=(3, 12, 7))
    registry.register(FakePlugin(make_manifest(requires=("python>=3.12", "api>=1"))))

    with pytest.raises(CompatibilityError):
        registry.register(
            FakePlugin(make_manifest(plugin_id="engine.old_py", requires=("python>=99",)))
        )
    with pytest.raises(CompatibilityError):
        registry.register(
            FakePlugin(make_manifest(plugin_id="engine.api", requires=("api>=2",)))
        )
    with pytest.raises(CompatibilityError):
        registry.register(
            FakePlugin(make_manifest(plugin_id="engine.cuda", requires=("cuda>=12",)))
        )


def test_plugin_dependency_must_be_registered_and_satisfy_version() -> None:
    """插件依赖：被依赖插件必须已注册且版本满足约束。"""
    registry = PluginRegistry()
    registry.register(
        FakePlugin(
            make_manifest(
                plugin_id="dep.tool", version="1.0.0", capabilities=("dep.classify",)
            )
        )
    )

    registry.register(
        FakePlugin(
            make_manifest(plugin_id="engine.ok", requires=("plugin:dep.tool>=1.0.0",))
        )
    )
    with pytest.raises(CompatibilityError):
        registry.register(
            FakePlugin(
                make_manifest(plugin_id="engine.greedy", requires=("plugin:dep.tool>=2.0.0",))
            )
        )
    with pytest.raises(CompatibilityError):
        registry.register(
            FakePlugin(
                make_manifest(plugin_id="engine.missing", requires=("plugin:ghost>=1.0.0",))
            )
        )


def test_conflicting_plugin_rejected() -> None:
    """conflicts 声明的插件已注册时拒绝。"""
    registry = PluginRegistry()
    registry.register(FakePlugin(make_manifest(plugin_id="engine.first")))
    with pytest.raises(CompatibilityError):
        registry.register(
            FakePlugin(
                make_manifest(
                    plugin_id="engine.second",
                    capabilities=("pdf.text.extract",),
                    conflicts=("engine.first",),
                )
            )
        )


@pytest.mark.parametrize(
    ("determinism", "cacheable"),
    [("deterministic", True), ("stochastic", False), ("model", False)],
)
def test_only_deterministic_plugins_allow_result_cache(
    determinism: str, cacheable: bool
) -> None:
    """determinism 非 deterministic 的插件禁启用结果缓存（验收 5）。"""
    registry = PluginRegistry()
    registry.register(FakePlugin(make_manifest(determinism=determinism)))
    assert registry.cacheable("pdf.classify") is cacheable


def test_invoke_goes_through_registry_with_frozen_context_only() -> None:
    """禁插件间互调：插件只拿到冻结的 PluginContext，不含注册表引用。"""
    captured: dict[str, PluginContext] = {}

    class SpyPlugin:
        manifest = make_manifest()

        def invoke(
            self, capability: str, payload: Mapping[str, Any], context: PluginContext
        ) -> Any:
            captured["context"] = context
            return payload

    registry = PluginRegistry()
    registry.register(SpyPlugin())
    context = PluginContext(task_id="task-1", doc_id="doc-1")

    assert registry.invoke("pdf.classify", {"value": 7}, context) == {"value": 7}
    got = captured["context"]
    assert got is context
    assert not hasattr(got, "registry")
    with pytest.raises(FrozenInstanceError):
        got.task_id = "other"  # type: ignore[misc]
