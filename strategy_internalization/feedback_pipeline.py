"""feedback_pipeline — 策略内化层反馈闭环驱动器（方案A）。

把 experiment.db 的 outcome 数据接上 feedback_log → lifecycle → 卡片 yaml，
让卡片能基于真实任务结果自动升降级。

闭环链路：
    experiment.db (outcome)
      → aggregate_card_stats（按 card_id 聚合统计）
      → sync_to_feedback_log（负反馈写入 negative_feedback 表，英→中映射）
      → evaluate_lifecycle（调 lifecycle.transition 算建议状态）
      → apply_lifecycle_decisions（写回 yaml status）
      → retriever 下次注入自动应用新 status/penalty

设计原则：
- 幂等：sync 用 task_id=request_id 去重，多次运行不重复写
- dry_run：run_pipeline(dry_run=True) 只报告不写文件
- 纯规则零 LLM：全部基于 SQL 聚合 + lifecycle 纯函数
"""
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from strategy_internalization import experiment, feedback_log, lifecycle
from strategy_internalization.models import StrategyCard

# outcome（英文）→ feedback_type（中文）映射
OUTCOME_TO_FEEDBACK_TYPE = {
    "retry": "重试",
    "user_corrected": "用户纠正",
    "tool_error": "工具报错",
}


# ── 数据结构 ──────────────────────────────────────────────────────

@dataclass
class CardFeedbackStats:
    """单张卡的反馈统计。"""
    card_id: str
    total_injected: int               # held_out=0 的曝光数
    total_with_outcome: int           # 有结果的曝光数
    negative_count: int               # 负反馈条数（retry/user_corrected/tool_error）
    negative_rate: float              # negative_count / total_with_outcome
    last_negative_ts: Optional[float]  # 最近一次负反馈的时间戳
    outcomes_detail: dict             # {"success": N, "retry": M, ...}


@dataclass
class SyncResult:
    """sync_to_feedback_log 的返回。"""
    added: int                        # 新写入条数
    skipped: int                      # 已存在跳过条数
    per_card: dict = field(default_factory=dict)  # {card_id: added_count}


@dataclass
class LifecycleDecision:
    """单张卡的生命周期决策。"""
    card_id: str
    current_status: str
    suggested_status: str
    event: lifecycle.LifecycleEvent
    negative_count: int
    days_since_last_negative: int


@dataclass
class ApplyResult:
    """apply_lifecycle_decisions 的返回。"""
    applied: int                      # 实际写回 yaml 的数量
    reported: int                     # 有变更建议的总数（含 dry_run）
    details: list = field(default_factory=list)  # [{card_id, old, new, applied}]


@dataclass
class PipelineReport:
    """run_pipeline 的完整报告。"""
    sync_result: SyncResult
    decisions: list
    apply_result: ApplyResult
    applied: int
    stats: dict  # {card_id: CardFeedbackStats}


# ── 1. 聚合 ──────────────────────────────────────────────────────

def aggregate_card_stats(experiment_db: str, card_id: str) -> CardFeedbackStats:
    """从 experiment.db 聚合单张卡的反馈统计（只看 held_out=0 的注入曝光）。"""
    conn = sqlite3.connect(experiment_db)
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT exposure.request_id, exposure.timestamp AS exp_ts,
                   outcome.outcome, outcome.timestamp AS out_ts
            FROM exposure
            LEFT JOIN outcome ON exposure.request_id = outcome.request_id
            WHERE exposure.card_id = ? AND exposure.held_out = 0
            ORDER BY exposure.timestamp DESC
            """,
            (card_id,),
        )
        rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

    total_injected = len(rows)
    with_outcome = [r for r in rows if r["outcome"] is not None]
    total_with_outcome = len(with_outcome)
    negative = [r for r in with_outcome if r["outcome"] in experiment.NEGATIVE_OUTCOMES]
    negative_count = len(negative)
    negative_rate = negative_count / total_with_outcome if total_with_outcome > 0 else 0.0
    # last_negative_ts 用 outcome.timestamp（负反馈发生时间，非曝光时间）
    last_negative_ts = max((r["out_ts"] for r in negative), default=None)

    outcomes_detail = {}
    for r in with_outcome:
        outcomes_detail[r["outcome"]] = outcomes_detail.get(r["outcome"], 0) + 1

    return CardFeedbackStats(
        card_id=card_id,
        total_injected=total_injected,
        total_with_outcome=total_with_outcome,
        negative_count=negative_count,
        negative_rate=negative_rate,
        last_negative_ts=last_negative_ts,
        outcomes_detail=outcomes_detail,
    )


def aggregate_all_cards(experiment_db: str) -> dict:
    """聚合 experiment.db 中所有出现过的 card_id 的统计。"""
    conn = sqlite3.connect(experiment_db)
    try:
        cur = conn.execute("SELECT DISTINCT card_id FROM exposure")
        card_ids = [row[0] for row in cur.fetchall()]
    finally:
        conn.close()
    return {cid: aggregate_card_stats(experiment_db, cid) for cid in card_ids}


# ── 2. 同步 outcome → feedback_log ───────────────────────────────

def sync_to_feedback_log(experiment_db: str, feedback_db: str) -> SyncResult:
    """把 experiment.db 的负 outcome 同步到 feedback_log.negative_feedback 表。

    幂等：用 (card_id, task_id=request_id) 去重，已存在的不重复写。
    held_out=1 的曝光不同步（对照组不能归因到卡）。
    success 不写入（只记负反馈）。
    """
    # 拿所有负反馈曝光（held_out=0 + outcome 是负面的）
    conn = sqlite3.connect(experiment_db)
    try:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" * len(OUTCOME_TO_FEEDBACK_TYPE))
        cur = conn.execute(
            f"""
            SELECT exposure.request_id, exposure.card_id, exposure.scenario,
                   exposure.timestamp, outcome.outcome
            FROM exposure
            LEFT JOIN outcome ON exposure.request_id = outcome.request_id
            WHERE exposure.held_out = 0
              AND outcome.outcome IN ({placeholders})
            """,
            tuple(OUTCOME_TO_FEEDBACK_TYPE.keys()),
        )
        rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

    # 查 feedback_log 已有的 (card_id, task_id) 对
    conn = sqlite3.connect(feedback_db)
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT card_id, task_id FROM negative_feedback")
        existing = {(r["card_id"], r["task_id"]) for r in cur.fetchall()}
    finally:
        conn.close()

    added = 0
    skipped = 0
    per_card = {}
    for r in rows:
        key = (r["card_id"], r["request_id"])
        if key in existing:
            skipped += 1
            continue
        feedback_log.log_negative_feedback(
            feedback_db,
            card_id=r["card_id"],
            task_id=r["request_id"],
            scenario=r["scenario"] or "",
            feedback_type=OUTCOME_TO_FEEDBACK_TYPE[r["outcome"]],
            card_text_hash="",
            timestamp=r["timestamp"],
        )
        added += 1
        per_card[r["card_id"]] = per_card.get(r["card_id"], 0) + 1
        existing.add(key)  # 防止同一次同步内重复

    return SyncResult(added=added, skipped=skipped, per_card=per_card)


# ── 3. 评估生命周期 ──────────────────────────────────────────────

def evaluate_lifecycle(feedback_db: str, cards_dir: str, now: Optional[float] = None) -> list:
    """对每张 active/watch 卡，读 feedback_log 算 negative_count + days_since，
    调 lifecycle.transition() 返回建议状态变更列表。"""
    if now is None:
        now = time.time()

    decisions = []
    for yaml_path in sorted(Path(cards_dir).glob("*.yaml")):
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
        if not data:
            continue
        status = data.get("status", "")
        if status not in ("active", "watch"):
            continue
        card_id = data["id"]

        neg_count = feedback_log.count_negative_feedback(feedback_db, card_id)
        recent = feedback_log.recent_feedback(feedback_db, card_id, limit=1)
        last_neg_ts = recent[0]["timestamp"] if recent else None

        if last_neg_ts is not None:
            days_since = int((now - last_neg_ts) / 86400)
        else:
            days_since = 999999  # 没有负反馈，相当于很久以前

        # 决定触发事件
        if status == "active":
            if neg_count > 0:
                event = lifecycle.LifecycleEvent.NEGATIVE_FEEDBACK
            else:
                event = lifecycle.LifecycleEvent.TIME_TICK  # 无负反馈，保持
        elif status == "watch":
            if last_neg_ts is not None and days_since < lifecycle.WATCH_RECOVERY_DAYS:
                # 仍有近期负反馈 → 可能隔离
                event = lifecycle.LifecycleEvent.NEGATIVE_FEEDBACK
            else:
                # 已过恢复期 → 可能回 active
                event = lifecycle.LifecycleEvent.TIME_TICK
        else:
            continue

        suggested = lifecycle.transition(
            status,
            event,
            negative_count=neg_count,
            days_since_last_negative=days_since,
        )

        decisions.append(LifecycleDecision(
            card_id=card_id,
            current_status=status,
            suggested_status=suggested,
            event=event,
            negative_count=neg_count,
            days_since_last_negative=days_since,
        ))

    return decisions


# ── 4. 应用状态变更 ──────────────────────────────────────────────

def apply_lifecycle_decisions(cards_dir: str, decisions: list, dry_run: bool = True) -> ApplyResult:
    """把状态变更写回卡片 yaml。dry_run=True 只报告不写。"""
    applied = 0
    reported = 0
    details = []

    for d in decisions:
        if d.suggested_status == d.current_status:
            continue  # 无变更

        reported += 1
        detail = {
            "card_id": d.card_id,
            "old": d.current_status,
            "new": d.suggested_status,
            "applied": False,
        }

        if dry_run:
            details.append(detail)
            continue

        # 写回 yaml
        yaml_path = Path(cards_dir) / f"{d.card_id}.yaml"
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
        data["status"] = d.suggested_status
        with open(yaml_path, "w") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

        applied += 1
        detail["applied"] = True
        details.append(detail)

    return ApplyResult(applied=applied, reported=reported, details=details)


# ── 5. 一键运行 ──────────────────────────────────────────────────

def run_pipeline(
    experiment_db: str,
    feedback_db: str,
    cards_dir: str,
    *,
    dry_run: bool = True,
) -> PipelineReport:
    """一键运行完整闭环：aggregate → sync → evaluate → apply。"""
    # 1. 聚合统计
    stats = aggregate_all_cards(experiment_db)

    # 2. 同步 outcome → feedback_log
    sync_result = sync_to_feedback_log(experiment_db, feedback_db)

    # 3. 评估生命周期
    decisions = evaluate_lifecycle(feedback_db, cards_dir)

    # 4. 应用变更
    apply_result = apply_lifecycle_decisions(cards_dir, decisions, dry_run=dry_run)

    return PipelineReport(
        sync_result=sync_result,
        decisions=decisions,
        apply_result=apply_result,
        applied=apply_result.applied,
        stats=stats,
    )
