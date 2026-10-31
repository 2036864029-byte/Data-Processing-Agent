"""错误码封闭集枚举。

宪法：「缺陷码是封闭集」——不得由模型自由生成；新增码必须走版本升级。
契约层叶子模块（无内部依赖）。
"""

from enum import StrEnum, unique


@unique
class DefectCode(StrEnum):
    """11 个缺陷码，逐字引用 contracts/defect-codes.md。"""

    ENCODING_LOSS = "encoding_loss"
    LOW_DENSITY = "low_density"
    REGION_EMPTY = "region_empty"
    TABLE_STRUCTURE_BROKEN = "table_structure_broken"
    TABLE_CROSS_PAGE_BREAK = "table_cross_page_break"
    READING_ORDER_BROKEN = "reading_order_broken"
    COLUMN_MIXED = "column_mixed"
    HEADER_FOOTER_LEAK = "header_footer_leak"
    PAGE_ROTATED = "page_rotated"
    PAGE_NUMBER_BROKEN = "page_number_broken"
    PLUGIN_REASON_UNKNOWN = "plugin_reason_unknown"
