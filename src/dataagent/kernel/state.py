"""状态机唯一合法转移表。

纯映射、无副作用（data-model.md「唯一合法转移表」）；含 REJECTED / PUBLISHED / ESCALATED 终态；
恢复语义 = 重新执行当前状态动作、每个状态动作幂等，**不新增恢复状态**。
"""

from enum import Enum, unique
from typing import Final


@unique
class State(Enum):
    """任务状态机全部状态（16 个，data-model.md 状态机表）。"""

    RECEIVED = "RECEIVED"
    REJECTED = "REJECTED"
    SNIFFED = "SNIFFED"
    CLASSIFIED = "CLASSIFIED"
    ROUTED = "ROUTED"
    PARSING = "PARSING"
    PARSED = "PARSED"
    POSTPROCESS = "POSTPROCESS"
    L1_CHECK = "L1_CHECK"
    L2_CHECK = "L2_CHECK"
    ATTRIBUTED = "ATTRIBUTED"
    REPAIRING = "REPAIRING"
    MERGED = "MERGED"
    L3_CHECK = "L3_CHECK"
    PUBLISHED = "PUBLISHED"
    ESCALATED = "ESCALATED"


@unique
class Event(Enum):
    """驱动转移的事件（按状态表「进入条件 / 出口」命名）。"""

    INTEGRITY_OK = "INTEGRITY_OK"
    INTEGRITY_FAIL = "INTEGRITY_FAIL"
    FAMILY_PDF = "FAMILY_PDF"
    FAMILY_OTHER = "FAMILY_OTHER"
    ROUTE_READY = "ROUTE_READY"
    QUEUED = "QUEUED"
    PARSE_DONE = "PARSE_DONE"
    TIER_T0 = "TIER_T0"
    TIER_T1 = "TIER_T1"
    TIER_T2_T3 = "TIER_T2_T3"
    POSTPROCESS_DONE = "POSTPROCESS_DONE"
    QC_PASS = "QC_PASS"
    QC_FAIL = "QC_FAIL"
    ACTION_T1 = "ACTION_T1"
    ACTION_T2_T3 = "ACTION_T2_T3"
    UNRESOLVED = "UNRESOLVED"
    REPARSE_DONE = "REPARSE_DONE"
    MERGE_DONE = "MERGE_DONE"


class InvalidTransitionError(ValueError):
    """非法状态转移（含终态出口与未定义事件）。"""


TRANSITIONS: Final[dict[tuple[State, Event], State]] = {
    (State.RECEIVED, Event.INTEGRITY_OK): State.SNIFFED,
    (State.RECEIVED, Event.INTEGRITY_FAIL): State.REJECTED,
    (State.SNIFFED, Event.FAMILY_PDF): State.CLASSIFIED,
    (State.SNIFFED, Event.FAMILY_OTHER): State.ROUTED,
    (State.CLASSIFIED, Event.ROUTE_READY): State.ROUTED,
    (State.ROUTED, Event.QUEUED): State.PARSING,
    (State.PARSING, Event.PARSE_DONE): State.PARSED,
    (State.PARSED, Event.TIER_T0): State.L1_CHECK,
    (State.PARSED, Event.TIER_T1): State.POSTPROCESS,
    (State.PARSED, Event.TIER_T2_T3): State.REPAIRING,
    (State.POSTPROCESS, Event.POSTPROCESS_DONE): State.L1_CHECK,
    (State.L1_CHECK, Event.QC_PASS): State.L2_CHECK,
    (State.L1_CHECK, Event.QC_FAIL): State.ATTRIBUTED,
    (State.L2_CHECK, Event.QC_PASS): State.MERGED,
    (State.L2_CHECK, Event.QC_FAIL): State.ATTRIBUTED,
    (State.ATTRIBUTED, Event.ACTION_T1): State.POSTPROCESS,
    (State.ATTRIBUTED, Event.ACTION_T2_T3): State.REPAIRING,
    (State.ATTRIBUTED, Event.UNRESOLVED): State.ESCALATED,
    (State.REPAIRING, Event.REPARSE_DONE): State.PARSING,
    (State.MERGED, Event.MERGE_DONE): State.L3_CHECK,
    (State.L3_CHECK, Event.QC_PASS): State.PUBLISHED,
    (State.L3_CHECK, Event.QC_FAIL): State.REPAIRING,  # 文档级校验失败 → 局部回退修复
}

TERMINAL_STATES: Final[frozenset[State]] = frozenset(
    {State.REJECTED, State.PUBLISHED, State.ESCALATED}
)


def transition(state: State, event: Event) -> State:
    """按唯一合法转移表推进；终态或未定义事件抛 InvalidTransitionError。"""
    if state in TERMINAL_STATES:
        raise InvalidTransitionError(f"终态 {state.name} 无出口转移（事件 {event.name}）")
    try:
        return TRANSITIONS[(state, event)]
    except KeyError:
        raise InvalidTransitionError(f"非法转移：{state.name} --{event.name}--> 未定义") from None
