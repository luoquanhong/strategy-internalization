"""卡片生命周期操作工具（G, P5）。

lifecycle.py 是状态机规则；本模块是可执行操作：promote / rollback + 审计日志。

约定：
- active 卡 id = shadow id 去掉 "shadow-" 前缀
- promote 后原 shadow 标记 retired（保留文件可追溯/可回滚），active 卡新增 source_shadow_id
- 操作幂等：对已 retired 的 shadow 再 promote 不重复创建
- 每次成功操作写一条 audit 日志（JSONL）
"""
from pathlib import Path
import json, time, yaml


def _new_active_id(shadow_id: str) -> str:
    return shadow_id[7:] if shadow_id.startswith("shadow-") else shadow_id


def _append_audit(audit_log, entry: dict):
    if audit_log:
        with open(audit_log, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def promote(shadow_id, shadow_dir, active_dir, audit_log=None) -> dict:
    """把 shadow 卡晋升为 active：创建 active 卡，原 shadow 标记 retired。

    幂等：若 shadow 已是 retired 或 active 卡已存在，直接返回不重复操作。
    """
    shadow_path = Path(shadow_dir) / f"{shadow_id}.yaml"
    if not shadow_path.exists():
        raise FileNotFoundError(f"shadow 卡不存在: {shadow_path}")

    data = yaml.safe_load(shadow_path.read_text()) or {}
    if data.get("status") == "retired":
        return {"action": "promote", "skipped": "already retired", "shadow_id": shadow_id}

    active_id = _new_active_id(shadow_id)
    active_path = Path(active_dir) / f"{active_id}.yaml"
    if active_path.exists():
        return {"action": "promote", "skipped": "active already exists", "shadow_id": shadow_id}

    # 创建 active 卡
    active_data = dict(data)
    active_data["id"] = active_id
    active_data["status"] = "active"
    active_data["source_shadow_id"] = shadow_id
    active_data["promoted_at"] = time.time()
    active_path.write_text(yaml.safe_dump(active_data, allow_unicode=True,
                                          sort_keys=False, default_flow_style=False))

    # 原 shadow 标记 retired
    data["status"] = "retired"
    shadow_path.write_text(yaml.safe_dump(data, allow_unicode=True,
                                          sort_keys=False, default_flow_style=False))

    _append_audit(audit_log, {
        "action": "promote", "shadow_id": shadow_id, "active_id": active_id,
        "timestamp": time.time(),
    })
    return {"action": "promote", "shadow_id": shadow_id, "active_id": active_id}


def rollback(active_id, shadow_dir, active_dir, audit_log=None) -> dict:
    """撤销 promote：删 active 卡，恢复对应 shadow 的 status=shadow。

    通过 active 卡的 source_shadow_id 找到原 shadow。
    """
    active_path = Path(active_dir) / f"{active_id}.yaml"
    if not active_path.exists():
        raise FileNotFoundError(f"active 卡不存在，无法回滚: {active_path}")

    active_data = yaml.safe_load(active_path.read_text()) or {}
    shadow_id = active_data.get("source_shadow_id")
    active_path.unlink()

    if shadow_id:
        shadow_path = Path(shadow_dir) / f"{shadow_id}.yaml"
        if shadow_path.exists():
            sdata = yaml.safe_load(shadow_path.read_text()) or {}
            sdata["status"] = "shadow"
            shadow_path.write_text(yaml.safe_dump(sdata, allow_unicode=True,
                                                  sort_keys=False, default_flow_style=False))

    _append_audit(audit_log, {
        "action": "rollback", "active_id": active_id, "shadow_id": shadow_id,
        "timestamp": time.time(),
    })
    return {"action": "rollback", "active_id": active_id, "shadow_id": shadow_id}


def get_audit_log(audit_log) -> list:
    """读取 audit 日志，返回 list[dict]。"""
    p = Path(audit_log)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().strip().splitlines() if l.strip()]
