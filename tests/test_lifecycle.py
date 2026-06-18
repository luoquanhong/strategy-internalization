"""P0-3 卡片生命周期状态机 TDD 测试（GPT-5.5 评审定稿）。

五态：draft → active → watch → quarantine → retired
GPT-5.5 流转规则（P0 只做状态机，watch 降权注入留 P1）：
1. 用户显式"不要这张卡"        → 任意状态 immediate quarantine
2. 单卡负反馈计数 ≥ 3 次        → active 转 watch
3. watch 卡 7 天无新负反馈     → watch 回 active
4. watch + 新负反馈            → watch 转 quarantine
5. draft 观察期满 7 天         → draft 转 active
6. 管理员显式 retire           → 任意状态 retired

P0 加载语义：只有 active 注入；draft/watch/quarantine/retired 都不注入（最保守）。
"""
import pytest
from strategy_internalization.lifecycle import (
    transition,
    is_injectable,
    VALID_STATUSES,
    INJECTABLE_STATUSES,
    LifecycleEvent,
)


def test_valid_statuses_cover_five_states():
    """五态齐全 + 兼容旧 shadow/archived。"""
    for s in ["draft", "active", "watch", "quarantine", "retired"]:
        assert s in VALID_STATUSES


def test_injectable_statuses_p1():
    """P1: active + watch 可降权注入；draft/quarantine/retired 不注入。
    旧合同(P0 only-active) → 新合同(P1 active+watch)，watch 降权逻辑在 retriever。"""
    assert is_injectable("active") is True
    assert is_injectable("watch") is True   # P1 升级
    for s in ["draft", "quarantine", "retired"]:
        assert is_injectable(s) is False
    assert INJECTABLE_STATUSES == {"active", "watch"}


def test_user_reject_immediate_quarantine():
    """规则1：用户显式拒绝 → 任意状态立即 quarantine。"""
    for cur in ["active", "watch", "draft"]:
        assert transition(cur, LifecycleEvent.USER_REJECT) == "quarantine"


def test_active_negative_threshold_to_watch():
    """规则2：active 卡负反馈累计 >= 3 → watch。"""
    assert transition("active", LifecycleEvent.NEGATIVE_FEEDBACK,
                       negative_count=3) == "watch"
    assert transition("active", LifecycleEvent.NEGATIVE_FEEDBACK,
                       negative_count=5) == "watch"


def test_active_below_threshold_stays_active():
    """规则2边界：active 负反馈未达阈值 → 保持 active。"""
    assert transition("active", LifecycleEvent.NEGATIVE_FEEDBACK,
                       negative_count=2) == "active"


def test_watch_recovers_to_active_after_7_days():
    """规则3：watch 卡 7 天无新负反馈 → 回 active。"""
    assert transition("watch", LifecycleEvent.TIME_TICK,
                      days_since_last_negative=7) == "active"
    assert transition("watch", LifecycleEvent.TIME_TICK,
                      days_since_last_negative=10) == "active"


def test_watch_not_recovered_before_7_days():
    """规则3边界：未满 7 天 → 保持 watch。"""
    assert transition("watch", LifecycleEvent.TIME_TICK,
                      days_since_last_negative=6) == "watch"


def test_watch_new_negative_to_quarantine():
    """规则4：watch 卡再收到新负反馈 → quarantine。"""
    assert transition("watch", LifecycleEvent.NEGATIVE_FEEDBACK,
                      negative_count=4) == "quarantine"


def test_draft_promotes_to_active_after_7_days():
    """规则5：draft 观察期满 7 天 → active。"""
    assert transition("draft", LifecycleEvent.TIME_TICK,
                      days_since_created=7) == "active"


def test_draft_stays_before_7_days():
    """规则5边界：draft 未满 7 天 → 保持 draft。"""
    assert transition("draft", LifecycleEvent.TIME_TICK,
                      days_since_created=3) == "draft"


def test_explicit_retire():
    """规则6：管理员显式 retire → retired。"""
    for cur in ["active", "watch", "quarantine"]:
        assert transition(cur, LifecycleEvent.RETIRE) == "retired"


def test_retired_is_terminal():
    """retired 是终态：任何事件都不再流转出去（除非人工复活，不在状态机内）。"""
    assert transition("retired", LifecycleEvent.NEGATIVE_FEEDBACK,
                      negative_count=99) == "retired"
    assert transition("retired", LifecycleEvent.TIME_TICK,
                      days_since_last_negative=99) == "retired"


def test_quarantine_stays_without_explicit_action():
    """quarantine 在无显式动作时保持（保留数据观察，等人工或 retire）。"""
    assert transition("quarantine", LifecycleEvent.TIME_TICK,
                      days_since_last_negative=99) == "quarantine"


def test_invalid_status_raises():
    """非法当前状态抛 ValueError。"""
    with pytest.raises(ValueError):
        transition("nonsense", LifecycleEvent.TIME_TICK)


def test_transition_is_pure_deterministic():
    """纯函数：同输入多次结果一致。"""
    a = transition("active", LifecycleEvent.NEGATIVE_FEEDBACK, negative_count=3)
    b = transition("active", LifecycleEvent.NEGATIVE_FEEDBACK, negative_count=3)
    assert a == b == "watch"
