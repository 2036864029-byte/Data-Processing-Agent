"""插件契约：Plugin Protocol + manifest schema。

逐字引用 contracts/plugin-manifest.md（PRD 5.1）：
- `provides[].capability` 命名 `域.动作`；`*.classify` 供路由消费、`*.extract` 供解析消费，
  二者不交叉（同一能力名不得同时含两个语义段）。
- `determinism` 必填，仅 `deterministic` 允许结果缓存（缓存键 = 内容哈希 + 参数哈希 + id@version）。
- `trust: unsigned` 必须声明 `sandbox` 非 `none`，且默认禁用、需显式开启。
- `api_version` 只接受当前内核支持的版本。

契约层叶子模块（仅标准库 + pydantic）。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum, unique
from typing import Any, Final, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

API_VERSION: Final[int] = 1

_CAPABILITY_RE: Final[re.Pattern[str]] = re.compile(
    r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$"
)
_PLUGIN_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$")
_VERSION_RE: Final[re.Pattern[str]] = re.compile(
    r"^\d+(?:\.\d+)*(?:[-+][0-9A-Za-z][0-9A-Za-z.-]*)?$"
)
_SEMANTIC_ACTIONS: Final[frozenset[str]] = frozenset({"classify", "extract"})


@unique
class PluginKind(StrEnum):
    ENGINE = "engine"
    VALIDATOR = "validator"
    EXTRACTOR = "extractor"
    CONNECTOR = "connector"
    EXPORTER = "exporter"


@unique
class Determinism(StrEnum):
    DETERMINISTIC = "deterministic"
    STOCHASTIC = "stochastic"
    MODEL = "model"


@unique
class ConcurrencyMode(StrEnum):
    REENTRANT = "reentrant"
    EXCLUSIVE = "exclusive"
    THREAD_UNSAFE = "thread_unsafe"


@unique
class TrustLevel(StrEnum):
    BUILTIN = "builtin"
    SIGNED = "signed"
    UNSIGNED = "unsigned"


@unique
class SandboxMode(StrEnum):
    NONE = "none"
    SUBPROCESS = "subprocess"
    CONTAINER = "container"
    REMOTE = "remote"


@unique
class CapabilityKind(StrEnum):
    """能力消费方：classify → 路由；extract → 解析；other → 其余动作。"""

    CLASSIFY = "classify"
    EXTRACT = "extract"
    OTHER = "other"


def capability_kind(capability: str) -> CapabilityKind:
    """按动作段判定能力消费方（`域.动作` 的最后一段）。"""
    action = capability.rpartition(".")[2]
    if action == CapabilityKind.CLASSIFY:
        return CapabilityKind.CLASSIFY
    if action == CapabilityKind.EXTRACT:
        return CapabilityKind.EXTRACT
    return CapabilityKind.OTHER


class IOSchema(BaseModel):
    """能力输入 / 输出 schema 引用。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_: str = Field(alias="schema")
    params: str | None = None


class Latency(BaseModel):
    model_config = ConfigDict(extra="forbid")

    p50_ms: float = Field(ge=0)
    unit: str


class CapabilitySignature(BaseModel):
    """能力签名：capability + 输入输出 schema + 延迟（contracts/plugin-manifest.md）。"""

    model_config = ConfigDict(extra="forbid")

    capability: str
    input: IOSchema
    output: IOSchema
    latency: Latency

    @model_validator(mode="after")
    def _validate_capability_name(self) -> Self:
        if not _CAPABILITY_RE.match(self.capability):
            raise ValueError(f"capability 必须命名为 `域.动作`（全小写点分）：{self.capability!r}")
        semantic = [part for part in self.capability.split(".") if part in _SEMANTIC_ACTIONS]
        if len(semantic) > 1:
            raise ValueError(f"classify 与 extract 不得交叉：{self.capability!r}")
        return self

    @property
    def kind(self) -> CapabilityKind:
        return capability_kind(self.capability)


class CostModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit: str = "page"
    classify_ms_p50: float = Field(default=0, ge=0)
    cpu_ms_p50: float = Field(default=0, ge=0)
    cpu_ms_p95: float = Field(default=0, ge=0)
    gpu_ms: float = Field(default=0, ge=0)
    memory_mb: int = Field(default=0, ge=0)


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_page_size_mb: float | None = Field(default=None, gt=0)
    timeout_s: float = Field(default=30.0, gt=0)


class Batching(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supported: bool = False
    default_batch_pages: int | None = Field(default=None, ge=1)
    max_batch_pages: int | None = Field(default=None, ge=1)
    batch_affinity: str | None = None

    @model_validator(mode="after")
    def _batch_sizes_consistent(self) -> Self:
        if (
            self.default_batch_pages is not None
            and self.max_batch_pages is not None
            and self.default_batch_pages > self.max_batch_pages
        ):
            raise ValueError("default_batch_pages 不得大于 max_batch_pages")
        return self


class OptionalDependency(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    required_when: list[str] = Field(default_factory=list)


class Compatibility(BaseModel):
    """依赖与兼容性声明：`requires` 形如 `python>=3.12` / `api>=1` / `plugin:<id>>=1.0.0`。"""

    model_config = ConfigDict(extra="forbid")

    requires: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)


class PluginManifest(BaseModel):
    """插件 manifest（contracts/plugin-manifest.md 逐字映射，未知字段一律拒绝）。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    version: str
    api_version: int
    kind: PluginKind
    provides: list[CapabilitySignature] = Field(min_length=1)
    cost_model: CostModel = Field(default_factory=CostModel)
    determinism: Determinism
    concurrency: ConcurrencyMode = ConcurrencyMode.REENTRANT
    trust: TrustLevel = TrustLevel.BUILTIN
    sandbox: SandboxMode = SandboxMode.NONE
    limits: Limits = Field(default_factory=Limits)
    batching: Batching = Field(default_factory=Batching)
    optional_deps: list[OptionalDependency] = Field(default_factory=list)
    compatibility: Compatibility = Field(default_factory=Compatibility)

    @field_validator("id")
    @classmethod
    def _validate_plugin_id(cls, value: str) -> str:
        if not _PLUGIN_ID_RE.match(value):
            raise ValueError(f"插件 id 必须形如 `域.名称`（全小写点分）：{value!r}")
        return value

    @field_validator("version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        if not _VERSION_RE.match(value):
            raise ValueError(f"版本号不合法：{value!r}")
        return value

    @field_validator("api_version")
    @classmethod
    def _validate_api_version(cls, value: int) -> int:
        if value != API_VERSION:
            raise ValueError(f"不支持的 api_version={value}（当前内核仅支持 {API_VERSION}）")
        return value

    @model_validator(mode="after")
    def _unique_capabilities(self) -> Self:
        names = [signature.capability for signature in self.provides]
        if len(names) != len(set(names)):
            raise ValueError("provides[].capability 不得重复声明")
        return self

    @model_validator(mode="after")
    def _unsigned_requires_sandbox(self) -> Self:
        if self.trust is TrustLevel.UNSIGNED and self.sandbox is SandboxMode.NONE:
            raise ValueError("trust: unsigned 必须声明 sandbox 非 none")
        return self

    @property
    def ref(self) -> str:
        """`id@version`：参与幂等键与结果缓存键。"""
        return f"{self.id}@{self.version}"

    @property
    def cacheable(self) -> bool:
        """仅 deterministic 允许结果缓存（验收 5）。"""
        return self.determinism is Determinism.DETERMINISTIC

    @property
    def enabled_by_default(self) -> bool:
        """unsigned 默认禁用、需显式开启（验收 4）。"""
        return self.trust is not TrustLevel.UNSIGNED


@dataclass(frozen=True, slots=True)
class PluginContext:
    """插件执行上下文：不含注册表引用（G3 禁插件间直接互调，必须经调度器）。"""

    task_id: str
    doc_id: str


@runtime_checkable
class Plugin(Protocol):
    """插件接口：manifest + 能力调用。

    插件只能看到 PluginContext，拿不到注册表，因此无法在插件间直接互调。
    """

    @property
    def manifest(self) -> PluginManifest: ...

    def invoke(
        self, capability: str, payload: Mapping[str, Any], context: PluginContext
    ) -> Any: ...


def manifest_from_document(document: Mapping[str, Any]) -> PluginManifest:
    """从 manifest 文件内容构造 manifest（兼容顶层 `manifest:` 包裹）。"""
    body = document.get("manifest", document)
    if not isinstance(body, Mapping):
        raise ValueError("manifest 必须是映射")
    return PluginManifest.model_validate(body)
