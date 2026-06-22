"""
策略内化层的实验管理模块。
提供曝光与结果的记录、查询以及基于结果计算惩罚值的功能。
使用 SQLite 作为存储后端，同步操作，无外部依赖。
"""

import sqlite3
import time
import random
from typing import Optional

# 有效结果集合
VALID_OUTCOMES = {"success", "retry", "user_corrected", "tool_error"}
# 负反馈结果集合
NEGATIVE_OUTCOMES = {"retry", "user_corrected", "tool_error"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS exposure (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    card_id TEXT NOT NULL,
    scenario TEXT NOT NULL DEFAULT '',
    held_out INTEGER NOT NULL DEFAULT 0,
    timestamp REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS outcome (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    timestamp REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_exp_card ON exposure(card_id);
CREATE INDEX IF NOT EXISTS idx_exp_req ON exposure(request_id);
CREATE INDEX IF NOT EXISTS idx_out_req ON outcome(request_id);
"""


def init_db(db_path: str) -> None:
    """初始化数据库，创建表与索引（幂等）。"""
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def record_exposure(
    db_path: str,
    request_id: str,
    card_id: str,
    scenario: str = "",
    held_out: bool = False,
    timestamp: Optional[float] = None,
) -> int:
    """记录一次曝光，返回新记录的自增 ID。"""
    ts = time.time() if timestamp is None else timestamp
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO exposure (request_id, card_id, scenario, held_out, timestamp) VALUES (?, ?, ?, ?, ?)",
            (request_id, card_id, scenario, 1 if held_out else 0, ts),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def record_outcome(
    db_path: str,
    request_id: str,
    outcome: str,
    timestamp: Optional[float] = None,
) -> int:
    """记录一次结果，返回新记录的自增 ID。若 outcome 不合法则抛出 ValueError。"""
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"Invalid outcome: {outcome!r}. Must be one of {VALID_OUTCOMES}")
    ts = time.time() if timestamp is None else timestamp
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO outcome (request_id, outcome, timestamp) VALUES (?, ?, ?)",
            (request_id, outcome, ts),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_outcome(db_path: str, request_id: str) -> Optional[str]:
    """获取指定 request_id 的最新结果（按时间倒序），若无则返回 None。"""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT outcome FROM outcome WHERE request_id = ? ORDER BY timestamp DESC LIMIT 1",
            (request_id,),
        )
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def recent_exposures_with_outcome(
    db_path: str, card_id: str, limit: int = 20, *, include_held_out: bool = False
) -> list:
    """获取指定卡片的最近曝光，左连接结果，按时间倒序。

    include_held_out=False（默认）：仅 held_out=0（实际注入的曝光），用于 penalty 计算。
    include_held_out=True：返回全部曝光（含 holdout 对照），用于分析/测试。
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        held_clause = "" if include_held_out else "AND exposure.held_out = 0"
        cur = conn.execute(
            f"""
            SELECT
                exposure.request_id,
                exposure.card_id,
                exposure.scenario,
                exposure.held_out,
                exposure.timestamp,
                outcome.outcome
            FROM exposure
            LEFT JOIN outcome ON exposure.request_id = outcome.request_id
            WHERE exposure.card_id = ? {held_clause}
            ORDER BY exposure.timestamp DESC
            LIMIT ?
            """,
            (card_id, limit),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def compute_card_penalty(
    db_path: str,
    card_id: str,
    *,
    window: int = 20,
    threshold: float = 0.4,
    min_exposures: int = 5,
) -> float:
    """
    计算卡片的惩罚值（0.5 或 1.0），基于最近 window 次曝光的结果。
    规则详见文档字符串。
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        # 获取最近 window 条 held_out=0 曝光，按时间倒序
        cur = conn.execute(
            """
            SELECT
                exposure.request_id,
                exposure.timestamp,
                outcome.outcome
            FROM exposure
            LEFT JOIN outcome ON exposure.request_id = outcome.request_id
            WHERE exposure.card_id = ? AND exposure.held_out = 0
            ORDER BY exposure.timestamp DESC
            LIMIT ?
            """,
            (card_id, window),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    total_exposures = len(rows)
    if total_exposures < min_exposures:
        return 1.0

    with_outcome = [r for r in rows if r["outcome"] is not None]
    total_with_outcome = len(with_outcome)
    if total_with_outcome == 0:
        return 1.0

    negative_count = sum(1 for r in with_outcome if r["outcome"] in NEGATIVE_OUTCOMES)
    negative_rate = negative_count / total_with_outcome
    if negative_rate >= threshold:
        return 0.5
    else:
        return 1.0


def should_holdout(
    card,
    *,
    now: Optional[float] = None,
    holdout_probability: float = 0.15,
    new_card_days: int = 7,
    rng=None,
) -> bool:
    """
    判断卡片是否应被 holdout（即不进入实验组）。
    参与条件：status == "watch" 或 promoted_at 在 new_card_days 天内。
    """
    if now is None:
        now = time.time()
    if rng is None:
        rng = random.random

    # 判断是否参与 holdout 逻辑
    if card.status == "watch":
        pass
    elif card.promoted_at is not None and (now - card.promoted_at) < new_card_days * 86400:
        pass
    else:
        return False

    # 参与后以 holdout_probability 概率返回 True
    return rng() < holdout_probability


def detect_and_log_retry(
    db_path: str,
    request_id: str,
    *,
    similarity_window: int = 300,
    now: Optional[float] = None,
) -> bool:
    """
    检测指定 request_id 是否有 held_out=0 的曝光。
    若有且尚无 outcome，则记录 outcome="retry"。
    返回是否检测到曝光（无论是否记录结果）。
    """
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT 1 FROM exposure WHERE request_id = ? AND held_out = 0 LIMIT 1",
            (request_id,),
        )
        exists = cur.fetchone() is not None
    finally:
        conn.close()

    if not exists:
        return False

    # 如果还没有 outcome，则记录 retry
    outcome = get_outcome(db_path, request_id)
    if outcome is None:
        record_outcome(db_path, request_id, "retry", timestamp=now)
    return True


def mark_stale_exposures_as_retry(
    db_path: str,
    *,
    window: int = 300,
    now: Optional[float] = None,
) -> int:
    """
    [已废弃] 批量标记超时无 outcome 的 held_out=0 曝光为 retry。

    保留向后兼容，但新代码请用 resolve_stale_exposures（双向信号）。

    原逻辑：超过 window 秒还没 outcome 的曝光 → retry。
    问题：chat 场景下几乎所有曝光最终都会超时，导致负反馈率永远 100%。
    """
    if now is None:
        now = time.time()
    cutoff = now - window
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            SELECT DISTINCT exposure.request_id
            FROM exposure
            LEFT JOIN outcome ON exposure.request_id = outcome.request_id
            WHERE exposure.held_out = 0
              AND exposure.timestamp < ?
              AND outcome.request_id IS NULL
            """,
            (cutoff,),
        )
        stale_rids = [row[0] for row in cur.fetchall()]
    finally:
        conn.close()

    for rid in stale_rids:
        record_outcome(db_path, rid, "retry", timestamp=now)
    return len(stale_rids)


def resolve_stale_exposures(
    db_path: str,
    *,
    boundary_seconds: int = 1800,
    now: Optional[float] = None,
) -> tuple:
    """
    双向信号：解决无 outcome 的 held_out=0 曝光，按时间分两类。

    - age < boundary_seconds → retry（用户很快回来，可能没解决）
    - age >= boundary_seconds → success（隔了 boundary 以上，大概率满意走了）

    有 outcome 的曝光不动（已有人工/自动记录的结果）。
    held_out=1 的对照组曝光不参与（没注入不能归因）。

    Returns:
        (retry_count, success_count)
    """
    if now is None:
        now = time.time()
    boundary = now - boundary_seconds

    conn = sqlite3.connect(db_path)
    try:
        # age < boundary → retry（timestamp >= boundary，即较近的）
        cur_retry = conn.execute(
            """
            SELECT DISTINCT exposure.request_id
            FROM exposure
            LEFT JOIN outcome ON exposure.request_id = outcome.request_id
            WHERE exposure.held_out = 0
              AND exposure.timestamp >= ?
              AND outcome.request_id IS NULL
            """,
            (boundary,),
        )
        retry_rids = [row[0] for row in cur_retry.fetchall()]

        # age >= boundary → success（timestamp < boundary，即较远的）
        cur_success = conn.execute(
            """
            SELECT DISTINCT exposure.request_id
            FROM exposure
            LEFT JOIN outcome ON exposure.request_id = outcome.request_id
            WHERE exposure.held_out = 0
              AND exposure.timestamp < ?
              AND outcome.request_id IS NULL
            """,
            (boundary,),
        )
        success_rids = [row[0] for row in cur_success.fetchall()]
    finally:
        conn.close()

    for rid in retry_rids:
        record_outcome(db_path, rid, "retry", timestamp=now)
    for rid in success_rids:
        record_outcome(db_path, rid, "success", timestamp=now)

    return (len(retry_rids), len(success_rids))
