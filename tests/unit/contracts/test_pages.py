"""页码基准转换点测试（T017，A3）——回归必写测试 #1。

宪法 II + research D1：系统内部一律 1-indexed，页码转换只发生在 contracts/pages.py。
page_from_plugin / page_to_internal round-trip；边界（第 1 页合法、第 0 页报错、末页）。
必写 #1：pdf-inspector 输出页码基准不一致的契约以仿真形状锁定，P0-B 接入真实引擎时沿用。
"""

import pytest

from dataagent.contracts import pages


def test_first_page_valid() -> None:
    """第 1 页合法：插件第 0 页 == 内部第 1 页。"""
    assert pages.page_from_plugin(0) == 1
    assert pages.page_to_internal(1) == 0


def test_internal_page_0_rejected() -> None:
    """内部第 0 页报错——系统内部一律 1-indexed。"""
    with pytest.raises(ValueError):
        pages.page_to_internal(0)


def test_negative_plugin_page_rejected() -> None:
    """负数插件页码报错（0-indexed 无第 -1 页）。"""
    with pytest.raises(ValueError):
        pages.page_from_plugin(-1)


def test_round_trip_plugin_to_internal_to_plugin() -> None:
    """插件页 → 内部 → 插件 round-trip 幂等。"""
    for k in range(0, 300):
        assert pages.page_to_internal(pages.page_from_plugin(k)) == k


def test_round_trip_internal_to_plugin_to_internal() -> None:
    """内部页 → 插件 → 内部 round-trip 幂等。"""
    for k in range(1, 301):
        assert pages.page_from_plugin(pages.page_to_internal(k)) == k


def test_last_page_maps_to_plugin_side() -> None:
    """末页：文档第 N 页在插件侧为 N-1。"""
    n = 137
    assert pages.page_to_internal(n) == n - 1
    assert pages.page_from_plugin(n - 1) == n


def test_plugin_page_basis_mismatch_anchor() -> None:
    """回归必写测试 #1（research D1）：pdf-inspector 页码基准不一致的契约。

    extract_pages_markdown(path, pages=[k]) 后 pages[0].page == k（0-indexed），
    同对象 pages_with_tables 是 1-indexed；同一页在两侧基准相差 1。
    唯一转换点 page_from_plugin(k) == k+1 使内部页码与 pages_with_tables 对齐；
    该页有表时 k+1 ∈ pages_with_tables。P0-B 接入真实 engine.pdf 时沿用同一断言。
    """

    # 仿真 pdf-inspector 输出形状：同一页在 pages[].page 为 k、在 pages_with_tables 为 k+1
    def simulate_plugin_extract(k: int, has_table: bool) -> tuple[int, list[int]]:
        plugin_pages = [{"page": k}]
        pages_with_tables = [k + 1] if has_table else []
        return plugin_pages[0]["page"], pages_with_tables

    for k in (0, 1, 7, 49):
        plugin_page, pages_with_tables = simulate_plugin_extract(k, has_table=True)
        assert plugin_page == k  # 原始插件输出 0-indexed
        assert pages.page_from_plugin(plugin_page) == k + 1  # 转换后 1-indexed
        assert k + 1 in pages_with_tables  # 该页有表 → k+1 出现在 pages_with_tables
