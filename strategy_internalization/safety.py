"""shadow 删除安全检查器（A, P7）。

删除残留旧版前，确认 shadow 卡没有 active 卡缺失的信息。
safe=True 当且仅当 shadow 的 keywords 和 actions 都是 active 的子集。
"""
from dataclasses import dataclass, field
from pathlib import Path
import yaml


@dataclass
class DeleteSafety:
    shadow_id: str
    active_id: str
    safe: bool
    shadow_only_keywords: list = field(default_factory=list)
    shadow_only_actions: list = field(default_factory=list)
    note: str = ""


def check_delete_safety(shadow_path, active_path) -> DeleteSafety:
    """对比 shadow 与 active 卡，判断删除 shadow 是否安全（无信息损失）。"""
    shadow = yaml.safe_load(Path(shadow_path).read_text()) or {}
    active = yaml.safe_load(Path(active_path).read_text()) or {}

    sk = set(shadow.get("trigger_keywords") or [])
    ak = set(active.get("trigger_keywords") or [])
    sa = set(shadow.get("actions") or [])
    aa = set(active.get("actions") or [])

    only_kw = sorted(sk - ak)
    only_act = sorted(sa - aa)
    safe = not only_kw and not only_act

    if safe:
        note = "shadow 是 active 的信息子集，可安全删除"
    else:
        note = f"shadow 有 active 没有的内容: 关键词{only_kw} 动作{only_act}"

    return DeleteSafety(
        shadow_id=shadow.get("id", ""),
        active_id=active.get("id", ""),
        safe=safe,
        shadow_only_keywords=only_kw,
        shadow_only_actions=only_act,
        note=note,
    )
