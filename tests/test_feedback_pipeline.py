"""feedback_pipeline 的 TDD 测试。

测试 feedback 闭环：experiment.db outcome → 聚合 → feedback_log → lifecycle → yaml 写回。
"""
import sqlite3
import time
import yaml
import os
from pathlib import Path

import pytest

from strategy_internalization import experiment, feedback_log, lifecycle
from strategy_internalization.feedback_pipeline import (
    aggregate_card_stats,
    aggregate_all_cards,
    sync_to_feedback_log,
    evaluate_lifecycle,
    apply_lifecycle_decisions,
    run_pipeline,
)


# ── helpers ──────────────────────────────────────────────────────

def setup_experiment_db(db_path, exposures, outcomes):
    """exposures: [(request_id, card_id, scenario, held_out, timestamp)]
    outcomes: [(request_id, outcome, timestamp)]"""
    experiment.init_db(db_path)
    for exp in exposures:
        experiment.record_exposure(db_path, *exp)
    for out in outcomes:
        experiment.record_outcome(db_path, *out)


def setup_feedback_db(db_path):
    feedback_log.init_db(db_path)


def write_card(cards_dir, card_id, status="active", priority=8, scenario_tags=None, keywords=None):
    card = {
        "id": card_id,
        "title": f"测试卡{card_id}",
        "scenario_tags": scenario_tags or ["bug_fix"],
        "trigger_keywords": keywords or ["报错"],
        "actions": ["动作1", "动作2"],
        "priority": priority,
        "status": status,
    }
    path = Path(cards_dir) / f"{card_id}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(card, f, allow_unicode=True)
    return str(path)


# ── 1. aggregate_card_stats ──────────────────────────────────────

class TestAggregateCardStats:
    def test_empty_db_returns_zero_stats(self, tmp_path):
        exp_db = str(tmp_path / "exp.db")
        setup_experiment_db(exp_db, [], [])
        stats = aggregate_card_stats(exp_db, "card-a")
        assert stats.card_id == "card-a"
        assert stats.total_injected == 0
        assert stats.negative_count == 0
        assert stats.negative_rate == 0.0
        assert stats.last_negative_ts is None

    def test_counts_only_injected_exposures_not_held_out(self, tmp_path):
        exp_db = str(tmp_path / "exp.db")
        now = time.time()
        # 2 条注入 + 1 条 holdout
        setup_experiment_db(exp_db, [
            ("req1", "card-a", "bug_fix", False, now - 100),
            ("req2", "card-a", "bug_fix", False, now - 50),
            ("req3", "card-a", "bug_fix", True, now - 30),   # held_out 不计
        ], [
            ("req1", "success", now - 90),
            ("req2", "retry", now - 40),
            ("req3", "success", now - 20),
        ])
        stats = aggregate_card_stats(exp_db, "card-a")
        assert stats.total_injected == 2      # held_out 排除
        assert stats.total_with_outcome == 2
        assert stats.negative_count == 1      # req2 是 retry
        assert stats.negative_rate == 0.5

    def test_last_negative_ts_is_most_recent_negative(self, tmp_path):
        exp_db = str(tmp_path / "exp.db")
        now = time.time()
        setup_experiment_db(exp_db, [
            ("req1", "card-a", "bug_fix", False, now - 100),
            ("req2", "card-a", "bug_fix", False, now - 50),
            ("req3", "card-a", "bug_fix", False, now - 10),
        ], [
            ("req1", "retry", now - 90),
            ("req2", "success", now - 40),
            ("req3", "tool_error", now - 5),
        ])
        stats = aggregate_card_stats(exp_db, "card-a")
        assert stats.last_negative_ts == now - 5   # req3 最近

    def test_outcomes_detail_breakdown(self, tmp_path):
        exp_db = str(tmp_path / "exp.db")
        now = time.time()
        setup_experiment_db(exp_db, [
            ("r1", "c1", "bug_fix", False, now - 40),
            ("r2", "c1", "bug_fix", False, now - 30),
            ("r3", "c1", "bug_fix", False, now - 20),
            ("r4", "c1", "bug_fix", False, now - 10),
        ], [
            ("r1", "success", now - 35),
            ("r2", "retry", now - 25),
            ("r3", "user_corrected", now - 15),
            ("r4", "tool_error", now - 5),
        ])
        stats = aggregate_card_stats(exp_db, "c1")
        assert stats.outcomes_detail["success"] == 1
        assert stats.outcomes_detail["retry"] == 1
        assert stats.outcomes_detail["user_corrected"] == 1
        assert stats.outcomes_detail["tool_error"] == 1

    def test_exposures_without_outcome_not_counted_in_rate(self, tmp_path):
        """无 outcome 的曝光不计入 negative_rate 分母。"""
        exp_db = str(tmp_path / "exp.db")
        now = time.time()
        setup_experiment_db(exp_db, [
            ("r1", "c1", "bug_fix", False, now - 40),
            ("r2", "c1", "bug_fix", False, now - 30),
        ], [
            ("r1", "retry", now - 35),
            # r2 无 outcome
        ])
        stats = aggregate_card_stats(exp_db, "c1")
        assert stats.total_injected == 2
        assert stats.total_with_outcome == 1
        assert stats.negative_count == 1
        assert stats.negative_rate == 1.0   # 1/1


# ── 2. aggregate_all_cards ───────────────────────────────────────

class TestAggregateAllCards:
    def test_multiple_cards_aggregated(self, tmp_path):
        exp_db = str(tmp_path / "exp.db")
        now = time.time()
        setup_experiment_db(exp_db, [
            ("r1", "c1", "bug_fix", False, now - 40),
            ("r2", "c2", "bug_fix", False, now - 30),
        ], [
            ("r1", "retry", now - 35),
            ("r2", "success", now - 25),
        ])
        result = aggregate_all_cards(exp_db)
        assert "c1" in result
        assert "c2" in result
        assert result["c1"].negative_count == 1
        assert result["c2"].negative_count == 0


# ── 3. sync_to_feedback_log ──────────────────────────────────────

class TestSyncToFeedbackLog:
    def test_outcome_mapped_to_chinese_feedback_type(self, tmp_path):
        exp_db = str(tmp_path / "exp.db")
        fb_db = str(tmp_path / "fb.db")
        now = time.time()
        setup_experiment_db(exp_db, [
            ("r1", "c1", "bug_fix", False, now - 40),
            ("r2", "c1", "bug_fix", False, now - 30),
            ("r3", "c1", "bug_fix", False, now - 20),
        ], [
            ("r1", "retry", now - 35),
            ("r2", "user_corrected", now - 25),
            ("r3", "tool_error", now - 15),
        ])
        setup_feedback_db(fb_db)
        result = sync_to_feedback_log(exp_db, fb_db)
        assert result.added == 3
        # 验证映射
        fb = feedback_log.recent_feedback(fb_db, "c1", limit=20)
        types = {f["feedback_type"] for f in fb}
        assert "重试" in types
        assert "用户纠正" in types
        assert "工具报错" in types

    def test_success_outcome_not_written(self, tmp_path):
        exp_db = str(tmp_path / "exp.db")
        fb_db = str(tmp_path / "fb.db")
        now = time.time()
        setup_experiment_db(exp_db, [
            ("r1", "c1", "bug_fix", False, now - 40),
        ], [
            ("r1", "success", now - 35),
        ])
        setup_feedback_db(fb_db)
        result = sync_to_feedback_log(exp_db, fb_db)
        assert result.added == 0
        assert feedback_log.count_negative_feedback(fb_db, "c1") == 0

    def test_idempotent_second_run_adds_zero(self, tmp_path):
        """跑两次只写一次——用 task_id=request_id 去重。"""
        exp_db = str(tmp_path / "exp.db")
        fb_db = str(tmp_path / "fb.db")
        now = time.time()
        setup_experiment_db(exp_db, [
            ("r1", "c1", "bug_fix", False, now - 40),
        ], [
            ("r1", "retry", now - 35),
        ])
        setup_feedback_db(fb_db)
        sync_to_feedback_log(exp_db, fb_db)  # 第一次
        result2 = sync_to_feedback_log(exp_db, fb_db)  # 第二次
        assert result2.added == 0
        assert result2.skipped == 1
        assert feedback_log.count_negative_feedback(fb_db, "c1") == 1

    def test_held_out_exposures_not_synced(self, tmp_path):
        """holdout 对照组的曝光不写入 feedback_log（没注入不能归因）。"""
        exp_db = str(tmp_path / "exp.db")
        fb_db = str(tmp_path / "fb.db")
        now = time.time()
        setup_experiment_db(exp_db, [
            ("r1", "c1", "bug_fix", False, now - 40),
            ("r2", "c1", "bug_fix", True, now - 30),  # held_out
        ], [
            ("r1", "retry", now - 35),
            ("r2", "tool_error", now - 25),
        ])
        setup_feedback_db(fb_db)
        result = sync_to_feedback_log(exp_db, fb_db)
        assert result.added == 1   # 只有 r1


# ── 4. evaluate_lifecycle ────────────────────────────────────────

class TestEvaluateLifecycle:
    def test_active_card_above_threshold_suggests_watch(self, tmp_path):
        """active 卡负反馈≥3 → 建议降 watch。"""
        cards_dir = str(tmp_path / "cards")
        write_card(cards_dir, "c1", status="active")
        fb_db = str(tmp_path / "fb.db")
        setup_feedback_db(fb_db)
        now = time.time()
        for i in range(3):
            feedback_log.log_negative_feedback(
                fb_db, card_id="c1", task_id=f"r{i}", scenario="bug_fix",
                feedback_type="重试", card_text_hash="h", timestamp=now - i * 100,
            )
        decisions = evaluate_lifecycle(fb_db, cards_dir, now=now)
        c1 = [d for d in decisions if d.card_id == "c1"][0]
        assert c1.current_status == "active"
        assert c1.suggested_status == "watch"
        assert c1.event == lifecycle.LifecycleEvent.NEGATIVE_FEEDBACK

    def test_active_card_below_threshold_stays_active(self, tmp_path):
        cards_dir = str(tmp_path / "cards")
        write_card(cards_dir, "c1", status="active")
        fb_db = str(tmp_path / "fb.db")
        setup_feedback_db(fb_db)
        now = time.time()
        for i in range(2):  # 只有2条，未达阈值3
            feedback_log.log_negative_feedback(
                fb_db, card_id="c1", task_id=f"r{i}", scenario="bug_fix",
                feedback_type="重试", card_text_hash="h", timestamp=now - i * 100,
            )
        decisions = evaluate_lifecycle(fb_db, cards_dir, now=now)
        c1 = [d for d in decisions if d.card_id == "c1"][0]
        assert c1.suggested_status == "active"  # 无变更

    def test_watch_card_no_negative_for_7days_suggests_active(self, tmp_path):
        cards_dir = str(tmp_path / "cards")
        write_card(cards_dir, "c1", status="watch")
        fb_db = str(tmp_path / "fb.db")
        setup_feedback_db(fb_db)
        now = time.time()
        # 8天前的负反馈（已过恢复期）
        feedback_log.log_negative_feedback(
            fb_db, card_id="c1", task_id="r0", scenario="bug_fix",
            feedback_type="重试", card_text_hash="h", timestamp=now - 8 * 86400,
        )
        decisions = evaluate_lifecycle(fb_db, cards_dir, now=now)
        c1 = [d for d in decisions if d.card_id == "c1"][0]
        assert c1.suggested_status == "active"
        assert c1.event == lifecycle.LifecycleEvent.TIME_TICK

    def test_watch_card_recent_negative_suggests_quarantine(self, tmp_path):
        cards_dir = str(tmp_path / "cards")
        write_card(cards_dir, "c1", status="watch")
        fb_db = str(tmp_path / "fb.db")
        setup_feedback_db(fb_db)
        now = time.time()
        feedback_log.log_negative_feedback(
            fb_db, card_id="c1", task_id="r0", scenario="bug_fix",
            feedback_type="工具报错", card_text_hash="h", timestamp=now - 10,
        )
        decisions = evaluate_lifecycle(fb_db, cards_dir, now=now)
        c1 = [d for d in decisions if d.card_id == "c1"][0]
        assert c1.suggested_status == "quarantine"


# ── 5. apply_lifecycle_decisions ─────────────────────────────────

class TestApplyLifecycleDecisions:
    def test_dry_run_does_not_write_yaml(self, tmp_path):
        cards_dir = str(tmp_path / "cards")
        card_path = write_card(cards_dir, "c1", status="active")
        fb_db = str(tmp_path / "fb.db")
        setup_feedback_db(fb_db)
        now = time.time()
        for i in range(3):
            feedback_log.log_negative_feedback(
                fb_db, card_id="c1", task_id=f"r{i}", scenario="bug_fix",
                feedback_type="重试", card_text_hash="h", timestamp=now - i,
            )
        decisions = evaluate_lifecycle(fb_db, cards_dir, now=now)
        result = apply_lifecycle_decisions(cards_dir, decisions, dry_run=True)
        # 验证 yaml 没被改
        with open(card_path) as f:
            data = yaml.safe_load(f)
        assert data["status"] == "active"  # 还是原样
        assert result.applied == 0
        assert result.reported == 1

    def test_real_run_writes_new_status_to_yaml(self, tmp_path):
        cards_dir = str(tmp_path / "cards")
        card_path = write_card(cards_dir, "c1", status="active")
        fb_db = str(tmp_path / "fb.db")
        setup_feedback_db(fb_db)
        now = time.time()
        for i in range(3):
            feedback_log.log_negative_feedback(
                fb_db, card_id="c1", task_id=f"r{i}", scenario="bug_fix",
                feedback_type="重试", card_text_hash="h", timestamp=now - i,
            )
        decisions = evaluate_lifecycle(fb_db, cards_dir, now=now)
        result = apply_lifecycle_decisions(cards_dir, decisions, dry_run=False)
        with open(card_path) as f:
            data = yaml.safe_load(f)
        assert data["status"] == "watch"  # 已写回
        assert result.applied == 1


# ── 6. run_pipeline（端到端） ────────────────────────────────────

class TestRunPipeline:
    def test_dry_run_pipeline_no_side_effects(self, tmp_path):
        """dry_run 全链路：聚合 + sync + evaluate + 报告，但不写 yaml。"""
        exp_db = str(tmp_path / "exp.db")
        fb_db = str(tmp_path / "fb.db")
        cards_dir = str(tmp_path / "cards")
        card_path = write_card(cards_dir, "c1", status="active")
        now = time.time()
        # v2：5次注入5次retry（100%率+样本5≥5），满足温和双门槛才会建议降级
        setup_experiment_db(exp_db, [
            ("r1", "c1", "bug_fix", False, now - 50),
            ("r2", "c1", "bug_fix", False, now - 40),
            ("r3", "c1", "bug_fix", False, now - 30),
            ("r4", "c1", "bug_fix", False, now - 20),
            ("r5", "c1", "bug_fix", False, now - 10),
        ], [
            ("r1", "retry", now - 45),
            ("r2", "retry", now - 35),
            ("r3", "retry", now - 25),
            ("r4", "retry", now - 15),
            ("r5", "retry", now - 5),
        ])
        setup_feedback_db(fb_db)
        report = run_pipeline(exp_db, fb_db, cards_dir, dry_run=True)
        # yaml 没被改
        with open(card_path) as f:
            assert yaml.safe_load(f)["status"] == "active"
        # 但报告里有变更建议
        assert len(report.decisions) == 1
        assert report.decisions[0].suggested_status == "watch"
        assert report.applied == 0

    def test_real_pipeline_writes_status_change(self, tmp_path):
        exp_db = str(tmp_path / "exp.db")
        fb_db = str(tmp_path / "fb.db")
        cards_dir = str(tmp_path / "cards")
        card_path = write_card(cards_dir, "c1", status="active")
        now = time.time()
        # v2：5次注入5次retry（100%率+样本5≥5），满足温和双门槛
        setup_experiment_db(exp_db, [
            ("r1", "c1", "bug_fix", False, now - 50),
            ("r2", "c1", "bug_fix", False, now - 40),
            ("r3", "c1", "bug_fix", False, now - 30),
            ("r4", "c1", "bug_fix", False, now - 20),
            ("r5", "c1", "bug_fix", False, now - 10),
        ], [
            ("r1", "retry", now - 45),
            ("r2", "retry", now - 35),
            ("r3", "retry", now - 25),
            ("r4", "retry", now - 15),
            ("r5", "retry", now - 5),
        ])
        setup_feedback_db(fb_db)
        report = run_pipeline(exp_db, fb_db, cards_dir, dry_run=False)
        with open(card_path) as f:
            assert yaml.safe_load(f)["status"] == "watch"
        assert report.applied == 1
