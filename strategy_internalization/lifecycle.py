"""卡片生命周期状态机（GPT-5.5 P0-3）。

五态：draft → active → watch → quarantine → retired
纯函数、确定性、零 LLM。供运维脚本 / cron 更新卡片 status 字段调用。

P0 范围（GPT-5.5 定稿）：只做状态机 + 注入门控。watch 卡降权注入是 P1，
P0 阶段 watch 一律不注入（最保守，避免坏卡继续污染）。
"""
from enum import Enum


class LifecycleEvent(Enum):
    """触发状态流转的事件。"""
    NEGATIVE_FEEDBACK = "negative_feedback"   # 收到一次负反馈（工具报错/用户纠正/重试）
    TIME_TICK = "time_tick"                   # 定时审计扫描（cron）
    USER_REJECT = "user_reject"               # 用户显式说"不要这张卡"
    RETIRE = "retire"                         # 管理员显式归档


# 五态 + 兼容旧值（shadow≈draft 观察期，archived≈retired）
VALID_STATUSES = frozenset({
    "draft", "active", "watch", "quarantine", "retired",
    "shadow", "archived",
})

# P1: active + watch 可注入（watch 降权逻辑在 retriever；P0 只有 active 最保守）
INJECTABLE_STATUSES = {"active", "watch"}

# 流转阈值（GPT-5.5 建议值，均可由 cron 配置覆盖）
NEGATIVE_TO_WATCH_THRESHOLD = 3       # active 负反馈累计达此值 → watch
WATCH_RECOVERY_DAYS = 7               # watch 无新负反馈满此天数 → 回 active
DRAFT_OBSERVE_DAYS = 7                # draft 观察期满此天数 → active


def is_injectable(status: str) -> bool:
    """该状态的卡片是否应注入 LLM packet。P0 仅 active。"""
    return status in INJECTABLE_STATUSES


def transition(
    current: str,
    event: LifecycleEvent,
    *,
    negative_count: int = 0,
    days_since_last_negative: int = 0,
    days_since_created: int = 0,
) -> str:
    """根据当前状态 + 事件返回新状态（纯函数）。

    Args:
        current: 当前状态（须在 VALID_STATUSES 内）
        event: 触发事件
        negative_count: 该卡累计负反馈次数（NEGATIVE_FEEDBACK 用）
        days_since_last_negative: 距上次负反馈天数（watch TIME_TICK 用）
        days_since_created: 距创建天数（draft TIME_TICK 用）

    Returns:
        新状态字符串。无流转条件时返回 current（幂等）。
    """
    if current not in VALID_STATUSES:
        raise ValueError(f"未知卡片状态: {current!r}")

    # retired 是终态，不在状态机内自动复活
    if current == "retired":
        return "retired"

    # 用户显式拒绝：任意状态立即隔离（规则1，最高优先级）
    if event == LifecycleEvent.USER_REJECT:
        return "quarantine"

    # 管理员显式归档（规则6）
    if event == LifecycleEvent.RETIRE:
        return "retired"

    if event == LifecycleEvent.NEGATIVE_FEEDBACK:
        if current == "active" and negative_count >= NEGATIVE_TO_WATCH_THRESHOLD:
            return "watch"            # 规则2
        if current == "watch":
            return "quarantine"       # 规则4：watch + 新负反馈 → 隔离
        return current

    if event == LifecycleEvent.TIME_TICK:
        if current == "watch" and days_since_last_negative >= WATCH_RECOVERY_DAYS:
            return "active"           # 规则3
        if current == "draft" and days_since_created >= DRAFT_OBSERVE_DAYS:
            return "active"           # 规则5
        return current

    return current
