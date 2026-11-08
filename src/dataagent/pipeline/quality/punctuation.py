"""终止标点集合（唯一来源）。

三处用法的语义不同，必须显式区分，不能再各写一串：

- `SENTENCE_END`：中文 / ASCII 句末与分隔标点。段落边界与伪表判定共用。
  刻意**不含** `.`——表格单元格里的小数点、英文缩写不应被当成句末。
- `BODY_TERMINAL`：正文硬换行回流用，在 `SENTENCE_END` 基础上补 `.` 与 `…`。
- `TEXT_METRIC_TERMINAL`：`prose_ratio` 统计用，额外补 `.`、收尾括号与逗号
  （“以标点收尾”比“句子结束”更宽）。
"""

from __future__ import annotations

from typing import Final

SENTENCE_END: Final[str] = "。！？；：!?;:"
BODY_TERMINAL: Final[str] = SENTENCE_END + ".…"
TEXT_METRIC_TERMINAL: Final[str] = SENTENCE_END + ".）)】」』,，"
