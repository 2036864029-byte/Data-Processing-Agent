"""必写测试 #2：未知原因透传（T098，B6）——G2。

映射表外 reason → plugin_reason_unknown，raw_reason 原样保留；
不得丢弃、不得凭猜测归入邻近码；映射按插件版本分组、单向。
"""

from dataagent.contracts.errors import DefectCode
from dataagent.pipeline.repair.reasons import (
    DEFAULT_REASON_MAPPING_PATH,
    ReasonMapper,
    ReasonStats,
    load_default_reason_mapper,
)
from dataagent.platform.observe.alerts import AlertEvaluator, AlertKind

TABLES = {
    "engine.pdf": {
        "1.0.0": {
            "low_char_density": DefectCode.LOW_DENSITY,
            "encoding_issue": DefectCode.ENCODING_LOSS,
        }
    }
}


def test_known_reason_maps_to_defect_code() -> None:
    mapper = ReasonMapper(TABLES)

    defect = mapper.map(
        plugin_id="engine.pdf",
        plugin_version="1.0.0",
        reason="low_char_density",
        page_no=7,
    )

    assert defect.code is DefectCode.LOW_DENSITY
    assert defect.raw_reason is None
    assert defect.location.page_no == 7
    assert defect.suggested_action is not None


def test_unknown_reason_preserved_verbatim() -> None:
    mapper = ReasonMapper(TABLES)
    raw = "Some_New_Plugin_Reason v2"

    defect = mapper.map(
        plugin_id="engine.pdf",
        plugin_version="1.0.0",
        reason=raw,
        page_no=7,
    )

    assert defect.code is DefectCode.PLUGIN_REASON_UNKNOWN
    assert defect.raw_reason == raw
    assert defect.suggested_action is None
    assert defect.location.page_no == 7


def test_unknown_plugin_version_goes_to_unknown_bucket() -> None:
    """按插件版本分组：升级后的新版本未配置 → 全部落未知桶（报告可看出升级影响）。"""
    mapper = ReasonMapper(TABLES)

    defect = mapper.map(
        plugin_id="engine.pdf",
        plugin_version="2.0.0",
        reason="low_char_density",
        page_no=1,
    )

    assert defect.code is DefectCode.PLUGIN_REASON_UNKNOWN
    assert defect.raw_reason == "low_char_density"


def test_neighbouring_codes_are_not_guessed() -> None:
    """未知原因不得凭猜测归入邻近码（如 low_density）。"""
    mapper = ReasonMapper(TABLES)

    defect = mapper.map(
        plugin_id="engine.pdf",
        plugin_version="1.0.0",
        reason="low_char_dens",  # 近似但未知
        page_no=1,
    )

    assert defect.code is DefectCode.PLUGIN_REASON_UNKNOWN


def test_unknown_ratio_feeds_alert() -> None:
    mapper = ReasonMapper(TABLES)
    reasons = ["low_char_density", "mystery_a", "mystery_b", "encoding_issue"]

    defects = [
        mapper.map(
            plugin_id="engine.pdf",
            plugin_version="1.0.0",
            reason=reason,
            page_no=index,
        )
        for index, reason in enumerate(reasons, start=1)
    ]
    stats = ReasonStats.from_defects(defects)

    assert stats.total == 4
    assert stats.unknown == 2
    assert stats.ratio == 0.5
    alerts = AlertEvaluator().evaluate(stats.alert_stats())
    assert any(alert.kind is AlertKind.PLUGIN_REASON_UNKNOWN_RATIO for alert in alerts)


def test_unknown_ratio_below_threshold_is_quiet() -> None:
    mapper = ReasonMapper(TABLES)
    defects = [
        mapper.map(
            plugin_id="engine.pdf",
            plugin_version="1.0.0",
            reason="low_char_density",
            page_no=index,
        )
        for index in range(1, 21)
    ]
    defects.append(
        mapper.map(
            plugin_id="engine.pdf",
            plugin_version="1.0.0",
            reason="mystery",
            page_no=99,
        )
    )

    stats = ReasonStats.from_defects(defects)

    assert stats.ratio < 0.05
    assert AlertEvaluator().evaluate(stats.alert_stats()) == ()


def test_default_mapping_config_loads() -> None:
    assert DEFAULT_REASON_MAPPING_PATH.is_file()

    mapper = load_default_reason_mapper()
    defect = mapper.map(
        plugin_id="engine.pdf",
        plugin_version="1.0.0",
        reason="low_char_density",
        page_no=1,
    )

    assert defect.code is DefectCode.LOW_DENSITY
