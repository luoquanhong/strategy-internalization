"""负反馈日志（GPT-5.5 P0-4）。

一张 sqlite 表，先只记不学——补齐可观测性，不做任何评分/学习/自动流转。
P1 阶段才在此基础上做"最近 20 次负反馈率"的简单规则评分。

feedback_type ∈ {工具报错, 用户纠正, 重试}
"""
import sqlite3
import time

VALID_FEEDBACK_TYPES = {"工具报错", "用户纠正", "重试"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS negative_feedback (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id         TEXT NOT NULL,
    task_id         TEXT,
    scenario        TEXT,
    feedback_type   TEXT NOT NULL,
    timestamp       REAL NOT NULL,
    card_text_hash  TEXT
);
CREATE INDEX IF NOT EXISTS idx_nf_card ON negative_feedback(card_id);
CREATE INDEX IF NOT EXISTS idx_nf_ts ON negative_feedback(timestamp);
"""


def init_db(db_path: str) -> None:
    """创建表（幂等）。"""
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def log_negative_feedback(
    db_path: str,
    *,
    card_id: str,
    task_id: str,
    scenario: str,
    feedback_type: str,
    card_text_hash: str,
    timestamp: float = None,
) -> int:
    """记录一条负反馈，返回新行 id。先记不学：不触碰任何卡片状态。"""
    if feedback_type not in VALID_FEEDBACK_TYPES:
        raise ValueError(
            f"非法 feedback_type: {feedback_type!r}，须为 {VALID_FEEDBACK_TYPES}"
        )
    ts = time.time() if timestamp is None else timestamp
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO negative_feedback "
            "(card_id, task_id, scenario, feedback_type, timestamp, card_text_hash) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (card_id, task_id, scenario, feedback_type, ts, card_text_hash),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def count_negative_feedback(db_path: str, card_id: str) -> int:
    """某卡累计负反馈次数。"""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT COUNT(*) FROM negative_feedback WHERE card_id = ?", (card_id,)
        )
        return cur.fetchone()[0]
    finally:
        conn.close()


def recent_feedback(db_path: str, card_id: str, limit: int = 20) -> list[dict]:
    """最近 N 条负反馈（按 timestamp 倒序），供 P1 规则评分使用。"""
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM negative_feedback WHERE card_id = ? "
            "ORDER BY timestamp DESC LIMIT ?",
            (card_id, limit),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()
