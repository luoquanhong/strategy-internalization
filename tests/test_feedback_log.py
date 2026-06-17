"""P0-4 负反馈日志 TDD 测试（GPT-5.5 评审定稿）。

GPT-5.5 P0-4：一张 sqlite 表，先只记不学，补齐可观测性。
表字段：id, card_id, task_id, scenario, feedback_type, timestamp, card_text_hash
feedback_type ∈ {工具报错, 用户纠正, 重试}
"先记不学"：本模块只负责写入和查询计数，不做任何评分/学习/自动流转。
"""
import sqlite3
import pytest
from strategy_internalization.feedback_log import (
    init_db,
    log_negative_feedback,
    count_negative_feedback,
    recent_feedback,
    VALID_FEEDBACK_TYPES,
)


def test_init_creates_table(tmp_path):
    db = str(tmp_path / "fb.db")
    init_db(db)
    conn = sqlite3.connect(db)
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='negative_feedback'")
    assert cur.fetchone() is not None
    # 字段齐全
    cols = {row[1] for row in conn.execute("PRAGMA table_info(negative_feedback)")}
    assert {"id", "card_id", "task_id", "scenario", "feedback_type",
            "timestamp", "card_text_hash"}.issubset(cols)
    conn.close()


def test_log_and_count(tmp_path):
    db = str(tmp_path / "fb.db")
    init_db(db)
    log_negative_feedback(db, card_id="quantify-bottleneck", task_id="t1",
                          scenario="refactor", feedback_type="工具报错",
                          card_text_hash="abc123")
    log_negative_feedback(db, card_id="quantify-bottleneck", task_id="t2",
                          scenario="refactor", feedback_type="用户纠正",
                          card_text_hash="abc123")
    log_negative_feedback(db, card_id="config-verify-format", task_id="t3",
                          scenario="ops_config", feedback_type="重试",
                          card_text_hash="def456")
    assert count_negative_feedback(db, "quantify-bottleneck") == 2
    assert count_negative_feedback(db, "config-verify-format") == 1
    assert count_negative_feedback(db, "never-logged") == 0


def test_invalid_feedback_type_rejected(tmp_path):
    db = str(tmp_path / "fb.db")
    init_db(db)
    with pytest.raises(ValueError):
        log_negative_feedback(db, card_id="c", task_id="t", scenario="s",
                              feedback_type="点赞", card_text_hash="h")


def test_valid_feedback_types():
    assert VALID_FEEDBACK_TYPES == {"工具报错", "用户纠正", "重试"}


def test_recent_feedback_window(tmp_path):
    db = str(tmp_path / "fb.db")
    init_db(db)
    for i in range(5):
        log_negative_feedback(db, card_id="c1", task_id=f"t{i}", scenario="refactor",
                              feedback_type="重试", card_text_hash="h")
    rows = recent_feedback(db, "c1", limit=3)
    assert len(rows) == 3
    # 返回最近的（按 timestamp 倒序），每条含 feedback_type
    assert all("feedback_type" in r for r in rows)


def test_log_does_not_mutate_card_status(tmp_path):
    """先记不学：写日志不触碰任何卡片状态/学习逻辑（仅验证函数无返回副作用契约）。"""
    db = str(tmp_path / "fb.db")
    init_db(db)
    ret = log_negative_feedback(db, card_id="c", task_id="t", scenario="s",
                                feedback_type="工具报错", card_text_hash="h")
    # 约定：返回新插入行 id（int），不返回任何"学习/流转"结果
    assert isinstance(ret, int)


def test_init_db_idempotent(tmp_path):
    """重复 init 不报错（IF NOT EXISTS）。"""
    db = str(tmp_path / "fb.db")
    init_db(db)
    init_db(db)
    log_negative_feedback(db, card_id="c", task_id="t", scenario="s",
                          feedback_type="重试", card_text_hash="h")
    assert count_negative_feedback(db, "c") == 1
