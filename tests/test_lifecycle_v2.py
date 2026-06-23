"""lifecycle v2 — 温和阈值测试（Phase 2 闭环 v2）。

问题背景（2026-06-23 真实降级事故）：
  原阈值 active→watch 用"绝对负反馈次数≥3"。
  真实数据下 no-blind-bypass-error 注入51次、负反馈8次(16%)被降级，
  verify-model-id 注入26次、负反馈4次(15%)被降级——
  负反馈率很低但因为注入频次高累计到3次就被冤降。

修复方向：
  active→watch 改用"负反馈率≥40% 且 样本≥5"（对齐 penalty 双门槛）。
  watch→quarantine 同理（避免 watch 卡只要有一次负反馈就被隔离）。
  原有绝对次数逻辑废弃但保留向后兼容（不传新参数时用旧逻辑）。
"""
import pytest
from strategy_internalization.lifecycle import (
    transition,
    NEGATIVE_TO_WATCH_THRESHOLD,
    NEGATIVE_RATE_THRESHOLD,
    MIN_EXPOSURES_FOR_LIFECYCLE,
    LifecycleEvent,
)


# ── 常量存在性 ──

def test_new_threshold_constants_exist():
    """v2 新增两个常量：负反馈率门槛 + 最小样本量。"""
    assert NEGATIVE_RATE_THRESHOLD == 0.4
    assert MIN_EXPOSURES_FOR_LIFECYCLE == 5


# ── active → watch：新逻辑（负反馈率 + 最小样本双门槛）──

def test_active_high_rate_enough_sample_to_watch():
    """active 负反馈率≥40% 且 样本≥5 → watch（新逻辑）。"""
    # 10次注入5次负反馈 = 50%率，样本10≥5 → 降级
    assert transition("active", LifecycleEvent.NEGATIVE_FEEDBACK,
                       negative_count=5, negative_rate=0.5,
                       total_injected=10) == "watch"


def test_active_high_rate_low_sample_stays_active():
    """active 负反馈率≥40% 但 样本<5 → 保持 active（样本不足不降级）。"""
    # 3次注入2次负反馈 = 67%率，但样本3<5 → 不降级
    assert transition("active", LifecycleEvent.NEGATIVE_FEEDBACK,
                       negative_count=2, negative_rate=0.67,
                       total_injected=3) == "active"


def test_active_low_rate_high_sample_stays_active():
    """active 负反馈率<40% 即使样本很多 → 保持 active（率高才降）。"""
    # 51次注入8次负反馈 = 16%率，样本51≥5 但率<40% → 不降级（修复冤降）
    assert transition("active", LifecycleEvent.NEGATIVE_FEEDBACK,
                       negative_count=8, negative_rate=0.16,
                       total_injected=51) == "active"


def test_active_boundary_rate_exactly_40pct_to_watch():
    """active 负反馈率正好40% 且 样本≥5 → watch（边界值，≥含等号）。"""
    # 5次注入2次负反馈 = 40%率，样本5≥5 → 降级
    assert transition("active", LifecycleEvent.NEGATIVE_FEEDBACK,
                       negative_count=2, negative_rate=0.40,
                       total_injected=5) == "watch"


# ── 向后兼容：不传新参数时走旧逻辑 ──

def test_legacy_no_rate_falls_back_to_count_threshold():
    """不传 negative_rate/total_injected 时，走旧的绝对次数≥3逻辑。"""
    # 旧逻辑：negative_count=3 → watch（不传rate/total）
    assert transition("active", LifecycleEvent.NEGATIVE_FEEDBACK,
                       negative_count=3) == "watch"
    # 旧逻辑：negative_count=2 → active
    assert transition("active", LifecycleEvent.NEGATIVE_FEEDBACK,
                       negative_count=2) == "active"


# ── watch → quarantine：新逻辑（同样用率+样本双门槛）──

def test_watch_high_rate_to_quarantine():
    """watch 负反馈率≥40% 且 样本≥5 → quarantine（新逻辑）。"""
    assert transition("watch", LifecycleEvent.NEGATIVE_FEEDBACK,
                       negative_count=6, negative_rate=0.5,
                       total_injected=12) == "quarantine"


def test_watch_low_rate_stays_watch():
    """watch 负反馈率<40% → 保持 watch（不再无脑隔离）。"""
    # 修复：原逻辑 watch+任何负反馈 → quarantine，太激进
    assert transition("watch", LifecycleEvent.NEGATIVE_FEEDBACK,
                       negative_count=3, negative_rate=0.15,
                       total_injected=20) == "watch"


def test_watch_high_rate_low_sample_stays_watch():
    """watch 负反馈率高但样本<5 → 保持 watch（样本不足不隔离）。"""
    assert transition("watch", LifecycleEvent.NEGATIVE_FEEDBACK,
                       negative_count=2, negative_rate=0.67,
                       total_injected=3) == "watch"


# ── watch → quarantine 旧逻辑兼容 ──

def test_watch_legacy_no_rate_falls_back_to_quarantine():
    """watch 不传新参数时，旧逻辑：有负反馈→quarantine（向后兼容）。"""
    assert transition("watch", LifecycleEvent.NEGATIVE_FEEDBACK,
                       negative_count=4) == "quarantine"


# ── 确定性 ──

def test_v2_transition_is_deterministic():
    """纯函数：同输入多次一致。"""
    kwargs = dict(negative_count=5, negative_rate=0.5, total_injected=10)
    a = transition("active", LifecycleEvent.NEGATIVE_FEEDBACK, **kwargs)
    b = transition("active", LifecycleEvent.NEGATIVE_FEEDBACK, **kwargs)
    assert a == b == "watch"
