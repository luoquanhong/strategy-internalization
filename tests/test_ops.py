"""G(P5): 卡片生命周期操作工具 TDD 测试。

lifecycle.py 是状态机规则；本模块是可执行操作（promote/merge/rollback）+ 审计日志。
操作在临时目录上跑（tmp_path），不碰真实卡片，每次操作写一条 audit 日志。
"""
import yaml, json
from strategy_internalization.ops import promote, rollback, get_audit_log
import time


def _card(cid, status="shadow", keywords=None, actions=None):
    return yaml.safe_dump({
        "id": cid, "title": cid, "scenario_tags": ["system_design"],
        "trigger_keywords": keywords or ["a"], "actions": actions or ["x"],
        "priority": 5, "status": status,
    }, allow_unicode=True, sort_keys=False)


def test_promote_creates_active_and_remarks_shadow(tmp_path):
    """promote: shadow→active，原 shadow 标记 retired，active 卡新增 source_shadow_id。"""
    shadow_d = tmp_path / "shadow"; shadow_d.mkdir()
    active_d = tmp_path / "active"; active_d.mkdir()
    audit = tmp_path / "audit.jsonl"
    (shadow_d / "shadow-x.yaml").write_text(_card("shadow-x", keywords=["a","b"]))
    promote("shadow-x", shadow_d, active_d, audit_log=audit)
    # active 卡已创建
    active = yaml.safe_load((active_d / "x.yaml").read_text())
    assert active["status"] == "active"
    assert active["source_shadow_id"] == "shadow-x"
    # 原 shadow 标记 retired（保留文件可追溯）
    shadow = yaml.safe_load((shadow_d / "shadow-x.yaml").read_text())
    assert shadow["status"] == "retired"


def test_promote_writes_audit_log(tmp_path):
    """promote 写一条 audit 日志，含 action/id/timestamp。"""
    shadow_d = tmp_path / "shadow"; shadow_d.mkdir()
    active_d = tmp_path / "active"; active_d.mkdir()
    audit = tmp_path / "audit.jsonl"
    (shadow_d / "shadow-x.yaml").write_text(_card("shadow-x"))
    promote("shadow-x", shadow_d, active_d, audit_log=audit)
    logs = [json.loads(l) for l in audit.read_text().strip().splitlines()]
    assert len(logs) == 1
    assert logs[0]["action"] == "promote"
    assert logs[0]["shadow_id"] == "shadow-x"
    assert logs[0]["active_id"] == "x"
    assert "timestamp" in logs[0]


def test_promote_idempotent_second_call_no_duplicate(tmp_path):
    """对已 retired 的 shadow 再 promote → 幂等，不重复创建 active，不重复写日志。"""
    shadow_d = tmp_path / "shadow"; shadow_d.mkdir()
    active_d = tmp_path / "active"; active_d.mkdir()
    audit = tmp_path / "audit.jsonl"
    (shadow_d / "shadow-x.yaml").write_text(_card("shadow-x"))
    promote("shadow-x", shadow_d, active_d, audit_log=audit)
    promote("shadow-x", shadow_d, active_d, audit_log=audit)  # 幂等
    logs = get_audit_log(audit)
    assert len(logs) == 1


def test_rollback_restores_shadow_and_removes_active(tmp_path):
    """rollback: 撤销 promote，恢复 shadow 状态，删 active 卡。"""
    shadow_d = tmp_path / "shadow"; shadow_d.mkdir()
    active_d = tmp_path / "active"; active_d.mkdir()
    audit = tmp_path / "audit.jsonl"
    (shadow_d / "shadow-x.yaml").write_text(_card("shadow-x"))
    promote("shadow-x", shadow_d, active_d, audit_log=audit)
    rollback("x", shadow_d, active_d, audit_log=audit)
    # active 卡已删
    assert not (active_d / "x.yaml").exists()
    # shadow 恢复 status=shadow
    shadow = yaml.safe_load((shadow_d / "shadow-x.yaml").read_text())
    assert shadow["status"] == "shadow"


def test_rollback_writes_audit_log(tmp_path):
    """rollback 写一条 action=rollback 日志。"""
    shadow_d = tmp_path / "shadow"; shadow_d.mkdir()
    active_d = tmp_path / "active"; active_d.mkdir()
    audit = tmp_path / "audit.jsonl"
    (shadow_d / "shadow-x.yaml").write_text(_card("shadow-x"))
    promote("shadow-x", shadow_d, active_d, audit_log=audit)
    rollback("x", shadow_d, active_d, audit_log=audit)
    logs = get_audit_log(audit)
    actions = [l["action"] for l in logs]
    assert "rollback" in actions


def test_rollback_fails_if_no_active(tmp_path):
    """rollback 不存在的 active 卡 → 抛错（不能凭空回滚）。"""
    shadow_d = tmp_path / "shadow"; shadow_d.mkdir()
    active_d = tmp_path / "active"; active_d.mkdir()
    audit = tmp_path / "audit.jsonl"
    import pytest
    with pytest.raises(FileNotFoundError):
        rollback("nonexistent", shadow_d, active_d, audit_log=audit)
