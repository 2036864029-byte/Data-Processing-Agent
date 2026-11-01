"""页码基准转换点（全工程仅一处）。

宪法 II + research D1：pdf-inspector 不同接口、甚至同一返回对象内部页码基准不一致
（extract_pages_markdown 的 pages[].page 是 0-indexed，同对象的 pages_with_tables 是 1-indexed）；
按字段级而非接口级转换是唯一正确做法。本模块是唯一页码转换点，系统内部一律 1-indexed。

page_from_plugin / page_to_internal 符号固定，供 CI grep 门禁匹配（scripts/check-gates.sh）；
调用方（包括插件适配器）使用下面的批量入口，不得在本模块之外引用转换符号或就地 ±1。
契约层叶子模块（无内部依赖）。
"""

from collections.abc import Sequence


def page_from_plugin(plugin_page: int) -> int:
    """插件页码 → 内部页码（0-indexed → 1-indexed）。

    page_from_plugin(0) == 1；负数插件页码拒绝（0-indexed 无第 -1 页）。
    """
    if plugin_page < 0:
        raise ValueError(f"插件页码必须 ≥ 0（0-indexed），收到 {plugin_page}")
    return plugin_page + 1


def page_to_internal(internal_page: int) -> int:
    """内部页码 → 插件页码（1-indexed → 0-indexed）。

    page_to_internal(1) == 0；内部第 0 页拒绝——系统内部一律 1-indexed。
    """
    if internal_page < 1:
        raise ValueError(f"内部页码必须 ≥ 1（1-indexed），收到 {internal_page}")
    return internal_page - 1


def plugin_page_indices(internal_pages: Sequence[int]) -> list[int]:
    """内部页码集合（1-indexed）→ 插件页码集合（0-indexed）。

    调用方进入插件接口的批量入口（如 pdf-inspector 的 `pages=[...]`）。
    """
    return [page_to_internal(page) for page in internal_pages]


def internal_page_numbers(plugin_pages: Sequence[int]) -> list[int]:
    """插件页码集合（0-indexed）→ 内部页码集合（1-indexed）。

    插件逐页返回对象（如 `pages[].page`）出站时的批量入口。
    """
    return [page_from_plugin(page) for page in plugin_pages]
