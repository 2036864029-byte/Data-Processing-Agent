"""Plan 错误类型（校验 / 指标缺失 / 分支未命中）。"""

from __future__ import annotations


class PlanError(ValueError):
    """Plan 相关错误基类。"""


class PlanValidationError(PlanError):
    """Plan 结构 / 表达式 / 引用不合法（生成期拒绝）。"""


class PlanMetricMissingError(PlanError):
    """执行期缺少决策所需统计指标。"""


class PlanBranchError(PlanError):
    """没有分支命中且无 else 兜底。"""
