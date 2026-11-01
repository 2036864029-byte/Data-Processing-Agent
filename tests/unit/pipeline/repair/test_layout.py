"""T1 版面修复测试（B6 扩展）：伪表格解包 + 中文编号层级重建。"""

from dataagent.pipeline.repair.layout import (
    is_plausible_table,
    normalize_layout,
    table_stats,
)

BODY = (
    "（一）根据不同地区、所有制形式、规模等设置差异化得分； "
    "（二）将业绩、市场占有率等不能反映企业信用状况的信息纳入信用评价范围； "
    "（三）在信用评价中设置无上限加分项； "
    "（四）其他违规设置交易壁垒的评价行为。 "
    "第十八条 国务院招标投标行政监督部门应当及时向社会公布本行业的公共信用评价结果。 "
    "二、申请人的资格要求： 1.满足政府采购法第二十二条规定； 2.落实政府采购政策。"
)
PSEUDO_TABLE = (
    "|2026/7/28 21:50|AI|||\n"
    "|---|---|---|---|\n"
    f"||{BODY}{BODY}||| \n"
)


def test_pseudo_table_is_unpacked() -> None:
    outcome = normalize_layout(PSEUDO_TABLE)

    assert outcome.unpacked_tables == 1
    assert "|2026/7/28 21:50|" not in outcome.markdown
    assert "第十八条" in outcome.markdown
    assert "（一）" in outcome.markdown
    assert outcome.headings_added >= 1


def test_chinese_numbering_rebuilds_paragraphs_and_headings() -> None:
    outcome = normalize_layout(
        "第六章 附则 第十八条 本办法自发布之日起施行。 二、申请人的资格要求： 1.满足规定；"
    )

    assert "# 第六章 附则" in outcome.markdown
    assert "## 二、申请人的资格要求：" in outcome.markdown
    assert "\n\n第十八条" in outcome.markdown
    assert outcome.headings_added == 2


def test_long_table_is_left_untouched() -> None:
    table = "|a|b|c|\n|---|---|---|\n" + "".join(
        f"|row{i}|{i * 2}|value{i}|\n" for i in range(40)
    )

    stats = table_stats(table)[0]
    outcome = normalize_layout(table)

    assert is_plausible_table(stats, page_chars=len(table)) is True
    assert outcome.unpacked_tables == 0
    assert outcome.markdown == table


def test_small_footer_table_is_not_unpacked() -> None:
    markdown = "正文段落一\n\n|站点|备案号|\n|---|---|\n|gov|ICP-1|\n"

    outcome = normalize_layout(markdown)

    assert outcome.unpacked_tables == 0
    assert "|站点|备案号|" in outcome.markdown


def test_normalization_is_idempotent() -> None:
    first = normalize_layout(PSEUDO_TABLE)

    second = normalize_layout(first.markdown)

    assert second.markdown == first.markdown
    assert second.unpacked_tables == 0


def test_text_outside_tables_is_preserved() -> None:
    markdown = "开头说明。 第十八条 正文内容。"

    outcome = normalize_layout(markdown)

    assert "开头说明。" in outcome.markdown
    assert "正文内容。" in outcome.markdown
