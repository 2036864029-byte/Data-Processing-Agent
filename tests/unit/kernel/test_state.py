"""状态机唯一合法转移表测试（T007，A2）。

data-model.md「唯一合法转移表」：全部合法转移断言唯一后继；非法转移抛错；
恢复语义 = 重新执行当前状态动作、每个状态动作幂等（不新增恢复状态）。
"""

import pytest

from dataagent.kernel.state import (
    TERMINAL_STATES,
    TRANSITIONS,
    Event,
    InvalidTransitionError,
    State,
    transition,
)

# 与 data-model.md「状态机（唯一合法转移表）」逐条对应
EXPECTED_TRANSITIONS: dict[tuple[State, Event], State] = {
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
    (State.L3_CHECK, Event.QC_FAIL): State.REPAIRING,
}


def test_all_documented_transitions_unique_successor() -> None:
    """全部合法转移断言唯一后继。"""
    assert EXPECTED_TRANSITIONS == TRANSITIONS
    for (state, event), target in EXPECTED_TRANSITIONS.items():
        assert transition(state, event) is target
    # 表中每条 (state, event) 唯一（dict 天然唯一，此处显式声明）
    assert len(TRANSITIONS) == len(set(TRANSITIONS))


def test_illegal_transition_raises() -> None:
    """非法转移（如 SNIFFED→PUBLISHED 类、真实事件用错状态）抛错。"""
    with pytest.raises(InvalidTransitionError):
        transition(State.SNIFFED, Event.QC_PASS)  # SNIFFED 无 QC 事件
    with pytest.raises(InvalidTransitionError):
        transition(State.RECEIVED, Event.QC_PASS)
    with pytest.raises(InvalidTransitionError):
        transition(State.RECEIVED, Event.PARSE_DONE)  # 真实事件用错状态


def test_unknown_event_raises() -> None:
    with pytest.raises(InvalidTransitionError):
        transition(State.PUBLISHED, Event.INTEGRITY_OK)  # 终态


def test_terminal_states_have_no_outgoing() -> None:
    """终态（REJECTED / PUBLISHED / ESCALATED）无任何出口转移。"""
    assert frozenset({State.REJECTED, State.PUBLISHED, State.ESCALATED}) == TERMINAL_STATES
    for state in TERMINAL_STATES:
        for event in Event:
            with pytest.raises(InvalidTransitionError):
                transition(state, event)


def test_no_recovery_states() -> None:
    """恢复语义 = 重新执行当前状态动作，不新增恢复状态：状态集与唯一转移表完全一致。"""
    assert len(State) == 16
    expected_states = {
        State.RECEIVED, State.REJECTED, State.SNIFFED, State.CLASSIFIED,
        State.ROUTED, State.PARSING, State.PARSED, State.POSTPROCESS,
        State.L1_CHECK, State.L2_CHECK, State.ATTRIBUTED, State.REPAIRING,
        State.MERGED, State.L3_CHECK, State.PUBLISHED, State.ESCALATED,
    }
    assert set(State) == expected_states


def test_every_nonterminal_state_has_outgoing_transition() -> None:
    sources = {state for state, _ in TRANSITIONS}
    for state in State:
        if state not in TERMINAL_STATES:
            assert state in sources


def test_transition_is_pure_and_repeatable() -> None:
    """纯映射、无副作用：同一 (state, event) 重复调用结果一致（幂等恢复的前提）。"""
    a = transition(State.RECEIVED, Event.INTEGRITY_OK)
    b = transition(State.RECEIVED, Event.INTEGRITY_OK)
    assert a is b
