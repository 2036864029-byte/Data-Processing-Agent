"""编号与标点完整性修复（确定性 T1 文本整形）。

针对 PDF 字形坐标排序导致的常见错序：
- `2（）` / `（）2` → `（2）`
- `（ 2 ）` / `(2）` / `（2)` → `（2）`
- 行首或中文标点后的半角 `(2)` → `（2）`
表格行不处理；幂等。
"""

from __future__ import annotations

import re
from typing import Final

# 数字后跟空括号：2（） / 2()
_EMPTY_AFTER: Final[re.Pattern[str]] = re.compile(r"(\d+)\s*[（(]\s*[)）]")
# 空括号后跟数字：（）2
_EMPTY_BEFORE: Final[re.Pattern[str]] = re.compile(r"[（(]\s*[)）]\s*(\d+)")
# 数字与空括号之间夹着“答：”：`1 答：（）` → `答：（1）`
_DIGIT_BEFORE_ANSWER: Final[re.Pattern[str]] = re.compile(
    r"(\d{1,3})\s*(答[:：])\s*[（(]\s*[)）]"
)
# 任意括号包数字（含空格/全半角混用，交由回调决定是否转换）
_PAREN_NUMBER: Final[re.Pattern[str]] = re.compile(
    r"[（(]\s*(\d{1,3})\s*[）)]"
)
# 行首或中文标点后的半角 (2)
_HALF_WIDTH: Final[re.Pattern[str]] = re.compile(
    r"(^[ \t]*|[一-鿿，。；：、（])\s*\(\s*(\d{1,3})\s*\)"
)
_TABLE_LINE: Final[re.Pattern[str]] = re.compile(r"^\s*\|")
# 数字串/编号错序的可疑模式（用于决定是否补探坐标项）
_SUSPICIOUS_INLINE: Final[re.Pattern[str]] = re.compile(
    r"[一-鿿]\s+\d{1,3}\s+\d{1,3}\s+[一-鿿]"
)
_SUSPICIOUS_ANSWER: Final[re.Pattern[str]] = re.compile(
    r"\d{1,3}\s*答[:：]\s*[（(]\s*[)）]"
)


def needs_position_probe(markdown: str) -> bool:
    """文本页出现数字串错序迹象时，值得补探坐标项供行重排。"""
    return bool(
        _SUSPICIOUS_INLINE.search(markdown)
        or _SUSPICIOUS_ANSWER.search(markdown)
    )


def repair_numbering(markdown: str) -> str:
    """修复编号括号错序/空格/半角问题；表格块原样保留。"""
    if not markdown.strip():
        return markdown
    output: list[str] = []
    for line in markdown.splitlines():
        if _TABLE_LINE.match(line):
            output.append(line)
            continue
        output.append(_repair_line(line))
    text = "\n".join(output)
    return text + "\n" if markdown.endswith("\n") else text


def malformed_numbering_count(markdown: str) -> int:
    """统计疑似错序/非规范编号出现次数（回归指标用）。"""
    count = 0
    for line in markdown.splitlines():
        if _TABLE_LINE.match(line):
            continue
        count += len(_EMPTY_AFTER.findall(line))
        count += len(_EMPTY_BEFORE.findall(line))
        for match in _PAREN_NUMBER.finditer(line):
            if _needs_canonical(match.group(0), match.group(1)):
                count += 1
        count += len(_HALF_WIDTH.findall(line))
    return count


def _repair_line(line: str) -> str:
    repaired = _EMPTY_AFTER.sub(r"（\1）", line)
    repaired = _EMPTY_BEFORE.sub(r"（\1）", repaired)
    repaired = _DIGIT_BEFORE_ANSWER.sub(r"\2（\1）", repaired)
    repaired = _PAREN_NUMBER.sub(
        lambda match: (
            f"（{match.group(1)}）"
            if _needs_canonical(match.group(0), match.group(1))
            else match.group(0)
        ),
        repaired,
    )
    repaired = _HALF_WIDTH.sub(lambda match: f"{match.group(1)}（{match.group(2)}）", repaired)
    return repaired


def _needs_canonical(text: str, number: str) -> bool:
    if text == f"（{number}）":
        return False
    if "（" in text or "）" in text:
        return True
    return any(char.isspace() for char in text)
