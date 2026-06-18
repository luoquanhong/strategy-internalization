"""
P1 experiment.py 的 TDD 测试。RED阶段，代码尚未实现，测试旨在描述期望行为。
"""
import time
import pytest
from strategy_internalization.experiment import (
    init_db,
    record_exposure,
    record_outcome,
    get_outcome,
    recent_exposures_with_outcome,
    compute_card_penalty,
    should_holdout,
    detect_and_log_retry,
    mark_stale_exposures_as_retry,
    VALID_OUTCOMES,
    NEGATIVE_OUTCOMES,
)
from strategy_internalization.models import StrategyCard


# ========= helpers ==========

def _make_card(status="active", promoted_at=None, **kwargs):
    """快速创建测试用的StrategyCard对象"""
    defaults = {
        "id": "card1",
        "title": "Test Card",
        "scenario_tags": [],
        "trigger_keywords": [],
        "actions": [],
        "priority": 1,
        "source": None,
    }
    defaults.update(kwargs)
    return StrategyCard(status=status, promoted_at=promoted_at, **defaults)


# 注：批量 seed helper 已移除（GLM 评审指出死代码）；当前测试用例内联构造数据。


# ========= init_db ==========

def test_init_db_idempotent(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    # 第二次调用不应报错
    init_db(db_path)

    import sqlite3
    conn = sqlite3.connect(db_path)
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('exposure','outcome')"
    ).fetchall()
    assert len(tables) == 2
    conn.close()


# ========= record_exposure ==========

def test_record_exposure_basic(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    rid = record_exposure(db_path, request_id="req1", card_id="cardA", scenario="on_error")
    assert isinstance(rid, int) and rid > 0

    import sqlite3
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT request_id, card_id, scenario, held_out FROM exposure WHERE id=?", (rid,)).fetchone()
    assert row is not None
    assert row[0] == "req1"
    assert row[1] == "cardA"
    assert row[2] == "on_error"
    assert row[3] == 0  # held_out=False 默认
    conn.close()


def test_record_exposure_held_out(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    rid = record_exposure(db_path, request_id="req2", card_id="cardB", held_out=True)
    import sqlite3
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT held_out FROM exposure WHERE id=?", (rid,)).fetchone()
    assert row[0] == 1
    conn.close()


def test_record_exposure_id_increment(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    id1 = record_exposure(db_path, request_id="r1", card_id="c1")
    id2 = record_exposure(db_path, request_id="r2", card_id="c1")
    assert id2 > id1


# ========= record_outcome ==========

def test_record_outcome_basic(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    oid = record_outcome(db_path, request_id="req1", outcome="success")
    assert isinstance(oid, int) and oid > 0

    import sqlite3
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT request_id, outcome FROM outcome WHERE id=?", (oid,)).fetchone()
    assert row == ("req1", "success")
    conn.close()


def test_record_outcome_invalid(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    for invalid in ["invalid", "bad", "SUCCESS", "", " "]:
        with pytest.raises(ValueError):
            record_outcome(db_path, request_id="reqX", outcome=invalid)


def test_record_outcome_id_increment(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    oid1 = record_outcome(db_path, request_id="r1", outcome="success")
    oid2 = record_outcome(db_path, request_id="r2", outcome="tool_error")
    assert oid2 > oid1


# ========= get_outcome ==========

def test_get_outcome_none(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    assert get_outcome(db_path, "no_such_req") is None


def test_get_outcome_returns_value(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    record_outcome(db_path, request_id="req1", outcome="user_corrected")
    assert get_outcome(db_path, "req1") == "user_corrected"


def test_get_outcome_most_recent(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    # 先记录旧的，再记录新的
    ts_old = 100.0
    ts_new = 200.0
    record_outcome(db_path, request_id="req1", outcome="success", timestamp=ts_old)
    record_outcome(db_path, request_id="req1", outcome="retry", timestamp=ts_new)
    assert get_outcome(db_path, "req1") == "retry"


# ========= recent_exposures_with_outcome ==========

def test_recent_exposures_with_outcome_only_injected(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    # 注入曝光
    record_exposure(db_path, request_id="req_inj", card_id="c1", held_out=False)
    # 对照曝光
    record_exposure(db_path, request_id="req_hold", card_id="c1", held_out=True)

    rows = recent_exposures_with_outcome(db_path, "c1", limit=10)
    # 只返回 held_out=0 的
    assert len(rows) == 1
    assert rows[0]["request_id"] == "req_inj"
    assert rows[0]["held_out"] == 0


def test_recent_exposures_with_outcome_join(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    record_exposure(db_path, request_id="r1", card_id="c1")
    record_outcome(db_path, request_id="r1", outcome="success")

    rows = recent_exposures_with_outcome(db_path, "c1", limit=10)
    assert len(rows) == 1
    assert rows[0]["request_id"] == "r1"
    assert rows[0]["outcome"] == "success"


def test_recent_exposures_with_outcome_none_outcome(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    record_exposure(db_path, request_id="r_no_out", card_id="c1")
    rows = recent_exposures_with_outcome(db_path, "c1", limit=10)
    assert rows[0]["outcome"] is None


def test_recent_exposures_order_and_limit(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    # 插入3条注入曝光，时间戳倒序插入但先记旧的
    base_ts = 1000.0
    for i in range(3):
        record_exposure(db_path, request_id=f"r{i}", card_id="c1", timestamp=base_ts + i)

    rows = recent_exposures_with_outcome(db_path, "c1", limit=2)
    assert len(rows) == 2
    # 按timestamp倒序，所以最近的先
    assert rows[0]["timestamp"] == base_ts + 2
    assert rows[1]["timestamp"] == base_ts + 1


# ========= compute_card_penalty ==========

def test_compute_card_penalty_insufficient_exposures(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    # 只有3次注入曝光，低于默认 min_exposures=5
    for i in range(3):
        record_exposure(db_path, request_id=f"r{i}", card_id="c1")
    assert compute_card_penalty(db_path, "c1") == 1.0


def test_compute_card_penalty_all_success(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    for i in range(6):
        req = f"r{i}"
        record_exposure(db_path, request_id=req, card_id="c1")
        record_outcome(db_path, request_id=req, outcome="success")
    assert compute_card_penalty(db_path, "c1") == 1.0


def test_compute_card_penalty_negative_threshold(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    # 5次注入曝光，4个有outcome，其中2个retry => 负反馈率 0.5 >= 0.4 -> 0.5
    for i in range(5):
        req = f"r{i}"
        record_exposure(db_path, request_id=req, card_id="c1")
    # 前两个retry，后两个success，最后一个不记outcome（None）
    record_outcome(db_path, request_id="r0", outcome="retry")
    record_outcome(db_path, request_id="r1", outcome="retry")
    record_outcome(db_path, request_id="r2", outcome="success")
    record_outcome(db_path, request_id="r3", outcome="success")
    # r4 无outcome
    assert compute_card_penalty(db_path, "c1") == 0.5


def test_compute_card_penalty_below_threshold(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    # 5次注入曝光，4个有outcome，1个retry => 0.25 < 0.4 -> 1.0
    for i in range(5):
        req = f"r{i}"
        record_exposure(db_path, request_id=req, card_id="c1")
    record_outcome(db_path, request_id="r0", outcome="retry")
    record_outcome(db_path, request_id="r1", outcome="success")
    record_outcome(db_path, request_id="r2", outcome="success")
    record_outcome(db_path, request_id="r3", outcome="success")
    assert compute_card_penalty(db_path, "c1") == 1.0


def test_compute_card_penalty_boundary(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    # 阈值边界：恰好负反馈率=0.4 -> 0.5
    # 5次曝光，5个outcome，2个retry -> 0.4
    for i in range(5):
        req = f"r{i}"
        record_exposure(db_path, request_id=req, card_id="c1")
        if i < 2:
            record_outcome(db_path, request_id=req, outcome="retry")
        else:
            record_outcome(db_path, request_id=req, outcome="success")
    assert compute_card_penalty(db_path, "c1") == 0.5


def test_compute_card_penalty_no_outcomes(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    # 所有曝光都没有outcome -> 分母0 -> 1.0
    for i in range(5):
        record_exposure(db_path, request_id=f"r{i}", card_id="c1")
    assert compute_card_penalty(db_path, "c1") == 1.0


def test_compute_card_penalty_excludes_heldout(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    # 混入对照组，它们不应影响评分
    # 注入曝光：5条，4个有outcome，2个retry => 率0.5 -> 0.5
    for i in range(5):
        req = f"r_inj{i}"
        record_exposure(db_path, request_id=req, card_id="c1", held_out=False)
    record_outcome(db_path, request_id="r_inj0", outcome="retry")
    record_outcome(db_path, request_id="r_inj1", outcome="retry")
    record_outcome(db_path, request_id="r_inj2", outcome="success")
    record_outcome(db_path, request_id="r_inj3", outcome="success")
    # 对照组：10条，全retry（如果被计入会拉高负反馈率）
    for i in range(10):
        req = f"r_hold{i}"
        record_exposure(db_path, request_id=req, card_id="c1", held_out=True)
        record_outcome(db_path, request_id=req, outcome="retry")
    assert compute_card_penalty(db_path, "c1") == 0.5


def test_compute_card_penalty_window(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    # 插入20条注入曝光，前15条全success，后5条全retry
    base_ts = 1000.0
    # 时间早的（前面15条）
    for i in range(15):
        req = f"early_{i}"
        timestamp = base_ts + i
        record_exposure(db_path, request_id=req, card_id="c1", timestamp=timestamp)
        record_outcome(db_path, request_id=req, outcome="success", timestamp=timestamp)
    # 时间晚的（后面5条）
    for i in range(5):
        req = f"late_{i}"
        timestamp = base_ts + 100 + i
        record_exposure(db_path, request_id=req, card_id="c1", timestamp=timestamp)
        record_outcome(db_path, request_id=req, outcome="retry", timestamp=timestamp)

    # window=5，只看最近5条（全retry） → 负反馈率1.0 -> 0.5
    assert compute_card_penalty(db_path, "c1", window=5) == 0.5
    # window=10，最近10条：5条retry + 5条success → 率0.5 -> 0.5
    assert compute_card_penalty(db_path, "c1", window=10) == 0.5
    # window=20，全部：5 retry, 15 success → 率 5/20 = 0.25 < 0.4 -> 1.0
    assert compute_card_penalty(db_path, "c1", window=20) == 1.0


# ========= should_holdout ==========

def test_should_holdout_active_mature():
    # 成熟 active，无promoted_at，应不参与
    card = _make_card(status="active")
    assert should_holdout(card) is False


def test_should_holdout_watch_injected():
    card = _make_card(status="watch")
    # rng 返回 0.1 (< 0.15) → True
    assert should_holdout(card, rng=lambda: 0.1) is True
    # rng 返回 0.9 (> 0.15) → False
    assert should_holdout(card, rng=lambda: 0.9) is False


def test_should_holdout_new_card_promoted():
    now = 1_000_000.0
    promoted_at = now - 2 * 86400  # 2天前
    card = _make_card(status="active", promoted_at=promoted_at)
    # 仍在观察期 new_card_days=7
    assert should_holdout(card, now=now, rng=lambda: 0.1) is True
    assert should_holdout(card, now=now, rng=lambda: 0.9) is False


def test_should_holdout_new_card_expired():
    now = 1_000_000.0
    promoted_at = now - 10 * 86400  # 10天前，超出默认7天
    card = _make_card(status="active", promoted_at=promoted_at)
    assert should_holdout(card, now=now) is False


def test_should_holdout_rng_above_probability():
    card = _make_card(status="watch")
    # rng 返回 0.15 等于阈值，应不算 True（因为<，不是<=）
    # SPEC: rng() < holdout_probability → True
    assert should_holdout(card, rng=lambda: 0.15) is False


def test_should_holdout_holdout_probability_parameter():
    card = _make_card(status="watch")
    # 设置 holdout_probability=0.5
    assert should_holdout(card, rng=lambda: 0.49, holdout_probability=0.5) is True
    assert should_holdout(card, rng=lambda: 0.5, holdout_probability=0.5) is False


# ========= detect_and_log_retry ==========

def test_detect_and_log_retry_no_exposure(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    assert detect_and_log_retry(db_path, "req_nonexist") is False


def test_detect_and_log_retry_with_exposure(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    record_exposure(db_path, request_id="req1", card_id="c1", held_out=False)
    result = detect_and_log_retry(db_path, "req1")
    assert result is True
    assert get_outcome(db_path, "req1") == "retry"


def test_detect_and_log_retry_existing_outcome_not_overwrite(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    record_exposure(db_path, request_id="req2", card_id="c1", held_out=False)
    record_outcome(db_path, request_id="req2", outcome="user_corrected")
    result = detect_and_log_retry(db_path, "req2")
    assert result is True
    assert get_outcome(db_path, "req2") == "user_corrected"


# ========= GLM 评审加固补丁 =========

def test_detect_and_log_retry_only_heldout_exposure(tmp_path):
    """GLM#1: 只有对照组曝光(held_out=1)时不应触发重试——契约限定 held_out=0。"""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    record_exposure(db_path, request_id="req_h", card_id="c1", held_out=True)
    result = detect_and_log_retry(db_path, "req_h")
    assert result is False
    assert get_outcome(db_path, "req_h") is None  # 不应记 outcome


def test_should_holdout_new_card_boundary():
    """GLM#2: now-promoted_at == new_card_days*86400 恰等边界 → 不参与（严格 <）。"""
    now = 1_000_000.0
    promoted_at = now - 7 * 86400  # 恰好7天，边界
    card = _make_card(status="active", promoted_at=promoted_at)
    # 恰等 → 不在观察期 → False（即使 rng 很小也不 holdout）
    assert should_holdout(card, now=now, rng=lambda: 0.01) is False


def test_compute_card_penalty_mixed_negative_types(tmp_path):
    """GLM#4: retry/user_corrected/tool_error 三种负反馈都应计入负反馈率。"""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    # 6次注入曝光，3种负反馈各1个 + 3个success → 负反馈率 3/6=0.5 >= 0.4 → 0.5
    negatives = ["retry", "user_corrected", "tool_error"]
    for i, out in enumerate(negatives):
        req = f"r_neg{i}"
        record_exposure(db_path, request_id=req, card_id="c1")
        record_outcome(db_path, request_id=req, outcome=out)
    for i in range(3):
        req = f"r_ok{i}"
        record_exposure(db_path, request_id=req, card_id="c1")
        record_outcome(db_path, request_id=req, outcome="success")
    assert compute_card_penalty(db_path, "c1") == 0.5


def test_mark_stale_exposures_as_retry(tmp_path):
    """超时无 outcome 的 held_out=0 曝光被标记为 retry，有 outcome 的不覆盖。"""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    now = time.time()

    # 3 条曝光：1 条超时无 outcome、1 条超时已有 success、1 条未超时无 outcome
    record_exposure(db_path, request_id="stale_no_outcome", card_id="c1",
                    timestamp=now - 600)  # 600s ago, > 300s window
    record_exposure(db_path, request_id="stale_has_outcome", card_id="c1",
                    timestamp=now - 600)
    record_outcome(db_path, request_id="stale_has_outcome", outcome="success", timestamp=now - 590)
    record_exposure(db_path, request_id="fresh_no_outcome", card_id="c1",
                    timestamp=now - 10)  # 10s ago, < 300s window

    marked = mark_stale_exposures_as_retry(db_path, window=300, now=now)
    assert marked == 1  # 只有 stale_no_outcome 被标记

    assert get_outcome(db_path, "stale_no_outcome") == "retry"
    assert get_outcome(db_path, "stale_has_outcome") == "success"  # 未覆盖
    assert get_outcome(db_path, "fresh_no_outcome") is None  # 未超时不标记
