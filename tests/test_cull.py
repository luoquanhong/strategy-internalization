"""D(P8): shadow 卡淘汰判定器 TDD 测试。

五指标（GPT-5.5 定），命中 ≥2 建议淘汰：
1. 长期零命中：hit_count=0 且存在 ≥30 天
2. 与 active 高重叠：≥6 关键词
3. 只提供泛化建议：action 具体性 <0.5
4. 场景触发不清：trigger_keywords <4 个
5. （与现有策略冲突：难自动判定，留人工，本版跳过）

保守原则：单指标不淘汰，≥2 指标才建议淘汰。
"""
import os, time, yaml
from strategy_internalization.cull import evaluate_culling, CullVerdict


def _write(d, cid, keywords, actions, status="shadow", age_days=0):
    p = d / f"{cid}.yaml"
    p.write_text(yaml.safe_dump({
        "id": cid, "title": cid, "scenario_tags": ["system_design"],
        "trigger_keywords": keywords, "actions": actions,
        "priority": 5, "status": status,
    }, allow_unicode=True, sort_keys=False))
    if age_days:
        t = time.time() - age_days * 86400
        os.utime(p, (t, t))


def test_zero_hit_old_card_triggers_indicator(tmp_path):
    """指标1：零命中 + 存在≥30天 → 该指标命中。"""
    shadow = tmp_path / "shadow"; shadow.mkdir()
    active = tmp_path / "active"; active.mkdir()
    _write(shadow, "s1", keywords=["a", "b", "c", "d"],
           actions=["具体动作一二三四五"], age_days=40)
    r = evaluate_culling(shadow, active, hits={"s1": 0})
    v = [x for x in r if x.id == "s1"][0]
    assert v.metrics["zero_hit_long_standing"] is True


def test_zero_hit_new_card_no_indicator(tmp_path):
    """指标1边界：零命中但存在<30天 → 不命中。"""
    shadow = tmp_path / "shadow"; shadow.mkdir()
    active = tmp_path / "active"; active.mkdir()
    _write(shadow, "s1", keywords=["a", "b", "c", "d"],
           actions=["具体动作一二三四五"], age_days=10)
    r = evaluate_culling(shadow, active, hits={"s1": 0})
    v = [x for x in r if x.id == "s1"][0]
    assert v.metrics["zero_hit_long_standing"] is False


def test_high_overlap_indicator(tmp_path):
    """指标2：与 active 重叠≥6 → 命中。"""
    shadow = tmp_path / "shadow"; shadow.mkdir()
    active = tmp_path / "active"; active.mkdir()
    kws = [f"k{i}" for i in range(8)]
    _write(active, "a1", keywords=kws, actions=["x"], status="active")
    _write(shadow, "s1", keywords=kws[:7], actions=["具体动作一二三四五"])  # 重叠7
    r = evaluate_culling(shadow, active)
    v = [x for x in r if x.id == "s1"][0]
    assert v.metrics["high_overlap_active"] is True


def test_generic_advice_indicator(tmp_path):
    """指标3：action 全是模糊词（具体性0）→ 命中。"""
    shadow = tmp_path / "shadow"; shadow.mkdir()
    active = tmp_path / "active"; active.mkdir()
    _write(shadow, "s1", keywords=["a", "b", "c", "d"],
           actions=["注意边界处理", "考虑适度解耦", "确保系统稳定"])
    r = evaluate_culling(shadow, active)
    v = [x for x in r if x.id == "s1"][0]
    assert v.metrics["only_generic_advice"] is True


def test_trigger_unclear_indicator(tmp_path):
    """指标4：trigger_keywords<4 → 命中。"""
    shadow = tmp_path / "shadow"; shadow.mkdir()
    active = tmp_path / "active"; active.mkdir()
    _write(shadow, "s1", keywords=["a", "b"], actions=["具体动作一二三四五"])  # 只有2个
    r = evaluate_culling(shadow, active)
    v = [x for x in r if x.id == "s1"][0]
    assert v.metrics["trigger_unclear"] is True


def test_two_indicators_recommend_cull(tmp_path):
    """命中≥2 → should_cull=True。"""
    shadow = tmp_path / "shadow"; shadow.mkdir()
    active = tmp_path / "active"; active.mkdir()
    kws = [f"k{i}" for i in range(8)]
    _write(active, "a1", keywords=kws, actions=["x"], status="active")
    _write(shadow, "s1", keywords=kws[:7],
           actions=["注意边界处理", "考虑适度解耦", "确保系统稳定"])  # 重叠7 + 泛化
    r = evaluate_culling(shadow, active)
    v = [x for x in r if x.id == "s1"][0]
    assert v.should_cull is True
    assert len(v.reasons) >= 2


def test_one_indicator_no_cull(tmp_path):
    """只命中1个指标 → should_cull=False（保守，单指标不淘汰）。"""
    shadow = tmp_path / "shadow"; shadow.mkdir()
    active = tmp_path / "active"; active.mkdir()
    _write(shadow, "s1", keywords=["a", "b"],  # 仅触发不清
           actions=["运行 pytest 写回归测试覆盖所有边界条件"])
    r = evaluate_culling(shadow, active)
    v = [x for x in r if x.id == "s1"][0]
    assert v.should_cull is False


def test_no_hits_entry_treated_as_zero(tmp_path):
    """hits 字典里没有该卡 → 当作零命中处理。"""
    shadow = tmp_path / "shadow"; shadow.mkdir()
    active = tmp_path / "active"; active.mkdir()
    _write(shadow, "s1", keywords=["a", "b", "c", "d"],
           actions=["具体动作一二三四五"], age_days=40)
    r = evaluate_culling(shadow, active, hits={})
    v = [x for x in r if x.id == "s1"][0]
    assert v.metrics["zero_hit_long_standing"] is True
