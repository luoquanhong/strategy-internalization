"""A(P7): shadow 删除安全检查器 TDD 测试。

删除残留旧版前，确认 shadow 卡没有 active 卡缺失的信息（关键词/动作）。
safe=True 当且仅当 shadow 的 keywords 和 actions 都是 active 的子集。
"""
import yaml
from strategy_internalization.safety import check_delete_safety, DeleteSafety


def _card(cid, keywords, actions, status="shadow", extra=None):
    d = {"id": cid, "title": cid, "scenario_tags": ["s"],
         "trigger_keywords": keywords, "actions": actions,
         "priority": 5, "status": status}
    if extra:
        d.update(extra)
    return yaml.safe_dump(d, allow_unicode=True, sort_keys=False)


def test_shadow_subset_of_active_is_safe(tmp_path):
    """shadow 是 active 的信息子集（active 多了 source_shadow_id/status）→ safe。"""
    sp = tmp_path / "shadow-x.yaml"
    ap = tmp_path / "x.yaml"
    kws = ["a", "b", "c"]; acts = ["动作1", "动作2"]
    sp.write_text(_card("shadow-x", kws, acts))
    ap.write_text(_card("x", kws, acts, status="active",
                        extra={"source_shadow_id": "shadow-x"}))
    r = check_delete_safety(sp, ap)
    assert r.safe is True


def test_shadow_extra_keyword_unsafe(tmp_path):
    """shadow 有 active 没有的关键词 → unsafe。"""
    sp = tmp_path / "shadow-x.yaml"; ap = tmp_path / "x.yaml"
    sp.write_text(_card("shadow-x", ["a", "b", "c", "EXTRA"], ["动作1"]))
    ap.write_text(_card("x", ["a", "b", "c"], ["动作1"], status="active"))
    r = check_delete_safety(sp, ap)
    assert r.safe is False
    assert "EXTRA" in r.shadow_only_keywords


def test_shadow_extra_action_unsafe(tmp_path):
    """shadow 有 active 没有的 action → unsafe。"""
    sp = tmp_path / "shadow-x.yaml"; ap = tmp_path / "x.yaml"
    sp.write_text(_card("shadow-x", ["a"], ["动作1", "EXTRA动作"]))
    ap.write_text(_card("x", ["a"], ["动作1"], status="active"))
    r = check_delete_safety(sp, ap)
    assert r.safe is False
    assert any("EXTRA" in a for a in r.shadow_only_actions)


def test_active_extra_fields_still_safe(tmp_path):
    """active 有 shadow 没有的字段（source_shadow_id/promoted_at）→ 仍 safe。"""
    sp = tmp_path / "shadow-x.yaml"; ap = tmp_path / "x.yaml"
    sp.write_text(_card("shadow-x", ["a"], ["动作1"]))
    ap.write_text(_card("x", ["a"], ["动作1"], status="active",
                       extra={"source_shadow_id": "shadow-x", "promoted_at": 123.0}))
    r = check_delete_safety(sp, ap)
    assert r.safe is True
