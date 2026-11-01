"""插件 manifest schema 测试（T031，A5）。

逐字引用 contracts/plugin-manifest.md：合法 engine.pdf manifest 通过；
缺 `determinism`、`trust: unsigned` 但 `sandbox: none` 拒绝（验收 1–5）；
`provides` 命名 `域.动作`，`*.classify` 与 `*.extract` 不交叉。
"""

import copy

import pytest
from pydantic import ValidationError

from dataagent.contracts.plugin import (
    API_VERSION,
    CapabilityKind,
    Determinism,
    PluginManifest,
    SandboxMode,
    TrustLevel,
    capability_kind,
    manifest_from_document,
)

# plugin-manifest.md「Schema（YAML）」示例逐字转写
ENGINE_PDF_MANIFEST: dict[str, object] = {
    "id": "engine.pdf",
    "name": "PDF 解析与分类插件",
    "version": "1.0.0",
    "api_version": 1,
    "kind": "engine",
    "provides": [
        {
            "capability": "pdf.classify",
            "input": {"schema": "doc.bytes"},
            "output": {"schema": "pdf.classification.v1"},
            "latency": {"p50_ms": 25, "unit": "document"},
        },
        {
            "capability": "pdf.text.extract",
            "input": {"schema": "doc.bytes", "params": "page_range"},
            "output": {"schema": "page.artifact.v1"},
            "latency": {"p50_ms": 16, "unit": "page"},
        },
    ],
    "cost_model": {
        "unit": "page",
        "classify_ms_p50": 25,
        "cpu_ms_p50": 16,
        "cpu_ms_p95": 40,
        "gpu_ms": 0,
        "memory_mb": 200,
    },
    "determinism": "deterministic",
    "concurrency": "reentrant",
    "trust": "builtin",
    "sandbox": "none",
    "limits": {"max_page_size_mb": 50, "timeout_s": 30},
    "batching": {
        "supported": True,
        "default_batch_pages": 32,
        "max_batch_pages": 50,
        "batch_affinity": "document",
    },
    "optional_deps": [
        {"name": "pdfium", "required_when": ["pdf.ocr"]},
        {"name": "onnxruntime", "required_when": ["pdf.ocr"]},
    ],
    "compatibility": {"requires": [], "conflicts": []},
}


def _manifest(**overrides: object) -> PluginManifest:
    data = copy.deepcopy(ENGINE_PDF_MANIFEST)
    data.update(overrides)
    return PluginManifest.model_validate(data)


def _with_capabilities(*names: str) -> dict[str, object]:
    data = copy.deepcopy(ENGINE_PDF_MANIFEST)
    data["provides"] = [
        {
            "capability": name,
            "input": {"schema": "doc.bytes"},
            "output": {"schema": "out.v1"},
            "latency": {"p50_ms": 1, "unit": "page"},
        }
        for name in names
    ]
    return data


def test_engine_pdf_manifest_valid() -> None:
    """plugin-manifest.md 示例原样通过，双 capability 与关键字段逐字一致。"""
    manifest = _manifest()
    assert manifest.id == "engine.pdf"
    assert manifest.version == "1.0.0"
    assert manifest.api_version == API_VERSION == 1
    assert [p.capability for p in manifest.provides] == ["pdf.classify", "pdf.text.extract"]
    assert [p.kind for p in manifest.provides] == [
        CapabilityKind.CLASSIFY,
        CapabilityKind.EXTRACT,
    ]
    assert manifest.determinism is Determinism.DETERMINISTIC
    assert manifest.trust is TrustLevel.BUILTIN
    assert manifest.sandbox is SandboxMode.NONE
    assert manifest.batching.supported is True
    assert manifest.batching.max_batch_pages == 50


def test_manifest_from_document_accepts_wrapped_document() -> None:
    """manifest 文件可带顶层 `manifest:` 键，也可直接是 manifest 体。"""
    wrapped = manifest_from_document({"manifest": ENGINE_PDF_MANIFEST})
    direct = manifest_from_document(ENGINE_PDF_MANIFEST)
    assert wrapped == direct


def test_missing_determinism_rejected() -> None:
    """determinism 缺失拒绝（验收 5：无法判定能否缓存 / 重试）。"""
    data = copy.deepcopy(ENGINE_PDF_MANIFEST)
    data.pop("determinism")
    with pytest.raises(ValidationError):
        PluginManifest.model_validate(data)


def test_unsigned_without_sandbox_rejected() -> None:
    """trust: unsigned 但 sandbox: none 拒绝（验收 4）。"""
    with pytest.raises(ValidationError):
        _manifest(trust="unsigned", sandbox="none")
    # unsigned + 非 none 沙箱合法，但默认禁用
    manifest = _manifest(trust="unsigned", sandbox="subprocess")
    assert manifest.enabled_by_default is False


def test_unsupported_api_version_rejected() -> None:
    """仅支持 api_version == 1；更高版本必须显式升级内核。"""
    with pytest.raises(ValidationError):
        _manifest(api_version=2)


@pytest.mark.parametrize(
    "capability",
    ["classify", "PDF.classify", "pdf..classify", "pdf.text-classify", ".classify", "pdf"],
)
def test_capability_naming_must_be_domain_action(capability: str) -> None:
    """provides[].capability 命名必须是 `域.动作`（全小写、点分）。"""
    with pytest.raises(ValidationError):
        PluginManifest.model_validate(_with_capabilities(capability))


def test_classify_and_extract_do_not_cross() -> None:
    """classify 与 extract 不交叉：同一能力名不得同时含两个语义段。"""
    with pytest.raises(ValidationError):
        PluginManifest.model_validate(_with_capabilities("pdf.classify.extract"))
    with pytest.raises(ValidationError):
        PluginManifest.model_validate(_with_capabilities("pdf.extract.classify"))


def test_duplicate_capability_rejected() -> None:
    """同一 manifest 内 capability 不得重复声明。"""
    with pytest.raises(ValidationError):
        PluginManifest.model_validate(
            _with_capabilities("pdf.classify", "pdf.classify")
        )


def test_capability_kind_classification() -> None:
    """`*.classify` 供路由、`*.extract` 供解析，其余动作归 OTHER。"""
    assert capability_kind("pdf.classify") is CapabilityKind.CLASSIFY
    assert capability_kind("pdf.text.extract") is CapabilityKind.EXTRACT
    assert capability_kind("pdf.ocr") is CapabilityKind.OTHER


@pytest.mark.parametrize(
    ("determinism", "cacheable"),
    [
        ("deterministic", True),
        ("stochastic", False),
        ("model", False),
    ],
)
def test_cacheable_only_when_deterministic(determinism: str, cacheable: bool) -> None:
    """仅 deterministic 允许结果缓存（验收 5）。"""
    assert _manifest(determinism=determinism).cacheable is cacheable


def test_batching_default_must_not_exceed_max() -> None:
    """批大小声明自洽：default_batch_pages 不得大于 max_batch_pages。"""
    with pytest.raises(ValidationError):
        _manifest(
            batching={
                "supported": True,
                "default_batch_pages": 64,
                "max_batch_pages": 50,
            }
        )
