"""文档级标题层级归一化测试（Phase 1）。

规则：
- 首个标题保持声明层级，不被提升也不会被压低；
- 无声明层级时用中文编号层级；
- 同一编号类型在同一文档内锁层（首个出现的层级为准，含跳级下调后的层级）；
- 只对“跳级”下调（H5 接在 H1 后 → H2），不做无条件提升。
"""

from dataagent.pipeline.structure.headings import HeadingLevelNormalizer


def test_first_heading_keeps_declared_level() -> None:
    """首个 `##` 不再被提升为 `#`（R-0023 根因）。"""
    normalizer = HeadingLevelNormalizer()

    assert normalizer.resolve("198. 某案例", 2) == 2


def test_same_numbering_kind_stays_same_level() -> None:
    normalizer = HeadingLevelNormalizer()

    assert normalizer.resolve("一、总则", 2) == 2
    assert normalizer.resolve("二、分则", 2) == 2


def test_locked_kind_wins_over_conflicting_declared_level() -> None:
    """同一编号模式必须同层：首个出现的层级锁住后续。"""
    normalizer = HeadingLevelNormalizer()

    assert normalizer.resolve("一、总则", 2) == 2
    assert normalizer.resolve("二、分则", 3) == 2


def test_numbering_level_used_when_no_declared_level() -> None:
    normalizer = HeadingLevelNormalizer()

    assert normalizer.resolve("第一章 总则", None) == 1
    assert normalizer.resolve("第一节 管辖", None) == 2
    assert normalizer.resolve("一、立案", None) == 2


def test_level_jump_is_clamped_downward() -> None:
    normalizer = HeadingLevelNormalizer()

    assert normalizer.resolve("一级", 1) == 1
    assert normalizer.resolve("五级", 5) == 2
    assert normalizer.resolve("再五级", 5) == 3


def test_no_unconditional_promotion() -> None:
    normalizer = HeadingLevelNormalizer()

    assert normalizer.resolve("三级", 3) == 3
    assert normalizer.resolve("二级", 2) == 2


def test_cross_page_continuation_does_not_depress_level() -> None:
    """跨页连续标题：前页末尾标题不应压低后页标题。"""
    normalizer = HeadingLevelNormalizer()

    assert normalizer.resolve("第一章", 1) == 1
    assert normalizer.resolve("一、总则", 2) == 2
    assert normalizer.resolve("二、分则", 2) == 2


def test_clamped_level_sticks_to_numbering_kind() -> None:
    """跳级下调后的层级写回锁定值，避免同类标题再次上浮。"""
    normalizer = HeadingLevelNormalizer()

    assert normalizer.resolve("第一章", 1) == 1
    assert normalizer.resolve("（一）立案", 3) == 2
    assert normalizer.resolve("（二）侦查", 3) == 2


def test_level_is_bounded_to_contract_range() -> None:
    normalizer = HeadingLevelNormalizer()

    assert normalizer.resolve("超纲标题", 9) == 6
