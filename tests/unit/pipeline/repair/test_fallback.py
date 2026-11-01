"""增强器降级测试（T102，B6）——FR-013。

外部增强器不可用时按配置降级（默认 mark_only）；报告显式声明降级路径；
`fallback.enhancer` 是配置项、不得写死（taskspec 校验规则 5）。
"""

import pytest

from dataagent.pipeline.repair.fallback import (
    EnhancerKind,
    FallbackConfig,
    FallbackPath,
    resolve_fallback,
)


def test_unavailable_enhancer_defaults_to_mark_only() -> None:
    decision = resolve_fallback(FallbackConfig(), available_enhancers=set())

    assert decision.path is FallbackPath.MARK_ONLY
    assert decision.auto_downstream_allowed is False
    assert "enhancer_unavailable" in decision.reason
    fields = decision.report_fields()
    assert fields["fallback_path"] == "mark_only"
    assert fields["auto_downstream"] == "false"
    assert "不可用于自动化下游" in fields["fallback_note"]


def test_available_enhancer_is_used() -> None:
    decision = resolve_fallback(
        FallbackConfig(), available_enhancers={EnhancerKind.LOCAL_OCR}
    )

    assert decision.path is FallbackPath.LOCAL_OCR
    assert decision.auto_downstream_allowed is True


def test_enhancer_is_configurable_not_hardcoded() -> None:
    config = FallbackConfig(enhancer=EnhancerKind.MINERU)

    decision = resolve_fallback(config, available_enhancers={EnhancerKind.MINERU})

    assert decision.path is FallbackPath.MINERU


def test_fail_doc_is_only_used_when_configured() -> None:
    decision = resolve_fallback(
        FallbackConfig(on_unavailable=FallbackPath.FAIL_DOC),
        available_enhancers=set(),
    )

    assert decision.path is FallbackPath.FAIL_DOC
    assert decision.auto_downstream_allowed is False


def test_none_enhancer_goes_straight_to_fallback() -> None:
    decision = resolve_fallback(
        FallbackConfig(enhancer=EnhancerKind.NONE), available_enhancers=set()
    )

    assert decision.path is FallbackPath.MARK_ONLY


def test_invalid_on_unavailable_rejected() -> None:
    with pytest.raises(ValueError):
        FallbackConfig(on_unavailable=FallbackPath.LOCAL_OCR)
