"""CJK 文本归一：伪空格去除 + 硬换行回流。

只做确定性文本整形，不生成/猜测内容：
- 汉字之间由抽取器插入的空格删除（中文与拉丁文之间的空格保留）；
- 不含终止标点的段落行合并，遇终止标点 / 编号 / 列表 / 标题 / 表格边界停止；
- 表格块原样保留；幂等，可重复执行。
"""

from __future__ import annotations

import re
from typing import Final

_TERMINAL: Final[str] = "。！？；：.!?;:…"
_CJK = (
    r"㐀-䶿一-鿿"
    r"，。！？；：、（）「」『』【】《》〈〉…—"
)
_PHANTOM_SPACE: Final[re.Pattern[str]] = re.compile(
    rf"(?<=[{_CJK}])[ \t]+(?=[{_CJK}])"
)
_NUMBERING: Final[re.Pattern[str]] = re.compile(
    r"^(第[一二三四五六七八九十百千0-9]+[条章节款]"
    r"|[一二三四五六七八九十]+、"
    r"|[（(]\d+[）)]"
    r"|[①-⑳]"
    r"|\d+[.、])"
)
_STRUCTURAL: Final[re.Pattern[str]] = re.compile(
    r"^(#{1,6}\s|[-*+]\s|>\s|\d+[.)]\s|```)"
)


def normalize_cjk_text(markdown: str) -> str:
    """返回归一后的 Markdown；输入为空或无需变化时原样返回。"""
    if not markdown.strip():
        return markdown
    normalized_newlines = markdown.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized_newlines.splitlines()
    output: list[str] = []
    current: str | None = None

    def flush() -> None:
        nonlocal current
        if current is not None:
            output.append(current)
            current = None

    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            flush()
            output.append("")
            index += 1
            continue

        if stripped.startswith("|"):
            flush()
            while index < len(lines) and lines[index].strip().startswith("|"):
                output.append(lines[index])
                index += 1
            continue

        cleaned = _strip_phantom_spaces(line)
        if _is_numbering(cleaned):
            flush()
            current = cleaned
            index += 1
            continue
        if _STRUCTURAL.match(cleaned.strip()):
            flush()
            output.append(cleaned)
            index += 1
            continue

        if current is not None and _can_join(current, cleaned):
            current = _join(current, cleaned)
        else:
            flush()
            current = cleaned
        index += 1

    flush()
    text = "\n".join(output)
    return text + "\n" if normalized_newlines.endswith("\n") else text


def _strip_phantom_spaces(line: str) -> str:
    """删汉字间伪空格，但保留编号标记后的结构空格（如“第二十八条 采购人”）。"""
    match = _NUMBERING.match(line.lstrip())
    protected = line
    if match is not None:
        prefix_end = len(line) - len(line.lstrip()) + match.end()
        if prefix_end < len(line) and line[prefix_end] == " ":
            protected = line[:prefix_end] + "\x00" + line[prefix_end + 1 :]
    cleaned = _PHANTOM_SPACE.sub("", protected)
    return cleaned.replace("\x00", " ")


def _is_numbering(line: str) -> bool:
    return bool(_NUMBERING.match(line.strip()))


def _is_structural(line: str) -> bool:
    stripped = line.strip()
    return bool(_NUMBERING.match(stripped) or _STRUCTURAL.match(stripped))


def _can_join(previous: str, line: str) -> bool:
    text = previous.rstrip()
    if not text:
        return False
    if text[-1] in _TERMINAL:
        return False
    return not _is_structural(line)


def _join(previous: str, line: str) -> str:
    left = previous.rstrip()
    right = line.lstrip()
    if not left:
        return right
    if not right:
        return left
    separator = "" if _is_cjk(left[-1]) and _is_cjk(right[0]) else " "
    return f"{left}{separator}{right}"


def _is_cjk(char: str) -> bool:
    return bool(re.match(rf"[{_CJK}]", char))
