"""resolve_stale_exposures 的 TDD 测试。

测试双向信号机制：无 outcome 的曝光按时间分两类。
< boundary → retry（用户很快回来，可能没解决）
>= boundary → success（隔很久，大概率解决）
"""
import time
import pytest

from strategy_internalization import experiment


def setup_db(db_path, exposures):
    """exposures: [(request_id, card_id, scenario, held_out, timestamp)]"""
    experiment.init_db(db_path)
    for exp in exposures:
        experiment.record_exposure(db_path, *exp)


class TestResolveStaleExposures:
    def test_recent_exposure_marked_retry(self, tmp_path):
        """距今 < boundary 的无结果曝光 → retry。"""
        db = str(tmp_path / "test.db")
        now = time.time()
        setup_db(db, [
            ("r1", "c1", "bug_fix", False, now - 300),  # 5分钟前
        ])
        retry_n, success_n = experiment.resolve_stale_exposures(db, boundary_seconds=1800, now=now)
        assert retry_n == 1
        assert success_n == 0
        assert experiment.get_outcome(db, "r1") == "retry"

    def test_old_exposure_marked_success(self, tmp_path):
        """距今 >= boundary 的无结果曝光 → success。"""
        db = str(tmp_path / "test.db")
        now = time.time()
        setup_db(db, [
            ("r1", "c1", "bug_fix", False, now - 3600),  # 1小时前
        ])
        retry_n, success_n = experiment.resolve_stale_exposures(db, boundary_seconds=1800, now=now)
        assert retry_n == 0
        assert success_n == 1
        assert experiment.get_outcome(db, "r1") == "success"

    def test_mixed_batch_some_retry_some_success(self, tmp_path):
        """一批曝光中，有的 retry 有的 success。"""
        db = str(tmp_path / "test.db")
        now = time.time()
        setup_db(db, [
            ("r1", "c1", "bug_fix", False, now - 100),    # 很近 → retry
            ("r2", "c1", "bug_fix", False, now - 1000),   # 中间 → retry
            ("r3", "c2", "bug_fix", False, now - 2000),   # 较远 → success
            ("r4", "c2", "bug_fix", False, now - 7200),   # 很远 → success
        ])
        retry_n, success_n = experiment.resolve_stale_exposures(db, boundary_seconds=1800, now=now)
        assert retry_n == 2
        assert success_n == 2

    def test_exposure_with_existing_outcome_not_touched(self, tmp_path):
        """已有 outcome 的曝光不被动。"""
        db = str(tmp_path / "test.db")
        now = time.time()
        setup_db(db, [
            ("r1", "c1", "bug_fix", False, now - 100),
        ])
        experiment.record_outcome(db, "r1", "tool_error", timestamp=now - 50)
        retry_n, success_n = experiment.resolve_stale_exposures(db, boundary_seconds=1800, now=now)
        assert retry_n == 0
        assert success_n == 0
        # 原来的 tool_error 不被覆盖
        assert experiment.get_outcome(db, "r1") == "tool_error"

    def test_held_out_exposures_not_resolved(self, tmp_path):
        """holdout 对照组的曝光不参与（held_out=1）。"""
        db = str(tmp_path / "test.db")
        now = time.time()
        setup_db(db, [
            ("r1", "c1", "bug_fix", True, now - 100),   # held_out
            ("r2", "c1", "bug_fix", False, now - 100),  # 注入的
        ])
        retry_n, success_n = experiment.resolve_stale_exposures(db, boundary_seconds=1800, now=now)
        assert retry_n == 1  # 只有 r2
        assert success_n == 0
        assert experiment.get_outcome(db, "r1") is None  # held_out 没被动

    def test_idempotent_second_run_resolves_zero(self, tmp_path):
        """第二次跑没有新的无结果曝光可处理。"""
        db = str(tmp_path / "test.db")
        now = time.time()
        setup_db(db, [
            ("r1", "c1", "bug_fix", False, now - 300),
        ])
        experiment.resolve_stale_exposures(db, boundary_seconds=1800, now=now)
        retry_n2, success_n2 = experiment.resolve_stale_exposures(db, boundary_seconds=1800, now=now)
        assert retry_n2 == 0
        assert success_n2 == 0

    def test_empty_db_returns_zeros(self, tmp_path):
        db = str(tmp_path / "test.db")
        experiment.init_db(db)
        retry_n, success_n = experiment.resolve_stale_exposures(db, boundary_seconds=1800)
        assert retry_n == 0
        assert success_n == 0
