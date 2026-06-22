"""B(P9): shadow 卡轻量分层器 TDD 测试。

三类（基于可测量信号，无 LLM，务实）：
- high_dup（强重复）：与 active 重叠 ≥6 关键词，或与其他 shadow 重叠 ≥8
- high_potential（高潜）：与 active 重叠 ≤3 且 actions 具体性 ≥0.5
- observe（待观察）：其余
"""
import yaml
from strategy_internalization.tier import tier_shadow_cards, TierReport


def _write(d, cid, keywords, actions, status="shadow"):
    """往目录 d 写一张卡。"""
    (d / f"{cid}.yaml").write_text(yaml.safe_dump({
        "id": cid, "title": cid, "scenario_tags": ["system_design"],
        "trigger_keywords": keywords, "actions": actions,
        "priority": 5, "status": status,
    }, allow_unicode=True, sort_keys=False))


def test_high_dup_with_active(tmp_path):
    """与 active 重叠 ≥6 关键词 → high_dup。"""
    active = tmp_path / "active"; active.mkdir()
    shadow = tmp_path / "shadow"; shadow.mkdir()
    kws = ["控制平面", "推理平面", "优化器", "编译器", "硬闸门", "解耦", "边界", "资源限制"]
    _write(active, "a1", keywords=kws, actions=["x"], status="active")
    _write(shadow, "s1", keywords=kws[:7], actions=["具体动作"])  # 重叠7
    r = tier_shadow_cards(active, shadow)
    assert "s1" in [c.id for c in r.high_dup]


def test_high_dup_with_sibling_shadow(tmp_path):
    """与其他 shadow 重叠 ≥8（互相克隆）→ high_dup。"""
    active = tmp_path / "active"; active.mkdir()
    shadow = tmp_path / "shadow"; shadow.mkdir()
    kws = [f"k{i}" for i in range(10)]
    _write(shadow, "s1", keywords=kws, actions=["具体动作"])
    _write(shadow, "s2", keywords=kws, actions=["具体动作"])  # 互相重叠10
    r = tier_shadow_cards(active, shadow)
    ids = [c.id for c in r.high_dup]
    assert "s1" in ids and "s2" in ids


def test_high_potential_low_overlap_specific(tmp_path):
    """重叠 ≤3 且 actions 具体（长且无模糊词）→ high_potential。"""
    active = tmp_path / "active"; active.mkdir()
    shadow = tmp_path / "shadow"; shadow.mkdir()
    _write(active, "a1", keywords=["alpha", "beta", "gamma", "delta"], actions=["x"], status="active")
    _write(shadow, "s1", keywords=["epsilon", "zeta", "eta"],  # 重叠0
           actions=["运行 benchmark 测量延迟并记录数值",
                    "用 pytest 写回归测试覆盖边界条件",
                    "把循环拆成独立函数并加结构化日志"])
    r = tier_shadow_cards(active, shadow)
    assert "s1" in [c.id for c in r.high_potential]


def test_observe_when_actions_fuzzy(tmp_path):
    """重叠低但 actions 全是模糊词（注意/考虑/确保）→ observe。"""
    active = tmp_path / "active"; active.mkdir()
    shadow = tmp_path / "shadow"; shadow.mkdir()
    _write(active, "a1", keywords=["alpha", "beta"], actions=["x"], status="active")
    _write(shadow, "s1", keywords=["gamma", "delta"],
           actions=["注意边界处理", "考虑适度解耦", "确保系统稳定"])
    r = tier_shadow_cards(active, shadow)
    assert "s1" in [c.id for c in r.observe]
    assert "s1" not in [c.id for c in r.high_potential]


def test_observe_middle_overlap(tmp_path):
    """重叠 4-5（不够强重复也不够独特）→ observe。"""
    active = tmp_path / "active"; active.mkdir()
    shadow = tmp_path / "shadow"; shadow.mkdir()
    kws = [f"k{i}" for i in range(10)]
    _write(active, "a1", keywords=kws, actions=["x"], status="active")
    _write(shadow, "s1", keywords=kws[:5], actions=["具体动作一二三四五"])  # 重叠5
    r = tier_shadow_cards(active, shadow)
    assert "s1" in [c.id for c in r.observe]


def test_all_shadow_cards_classified_exactly_once(tmp_path):
    """每张 shadow 卡恰好归入一类，三类并集 = 全部 shadow。"""
    active = tmp_path / "active"; active.mkdir()
    shadow = tmp_path / "shadow"; shadow.mkdir()
    _write(active, "a1", keywords=["a", "b", "c"], actions=["x"], status="active")
    _write(shadow, "s1", keywords=["a", "b", "c", "d", "e", "f", "g"], actions=["x"])  # dup
    _write(shadow, "s2", keywords=["z1", "z2"], actions=["运行基准测试记录数值", "写文档说明"])  # high_pot
    _write(shadow, "s3", keywords=["a", "b", "c", "d"], actions=["x"])  # overlap4 observe
    r = tier_shadow_cards(active, shadow)
    union = {c.id for c in r.high_dup} | {c.id for c in r.observe} | {c.id for c in r.high_potential}
    assert union == {"s1", "s2", "s3"}
    # 无重复归类
    assert len(union) == len(r.high_dup) + len(r.observe) + len(r.high_potential)


def test_reason_is_human_readable(tmp_path):
    """分层理由是人话，含可追溯的具体数值。"""
    active = tmp_path / "active"; active.mkdir()
    shadow = tmp_path / "shadow"; shadow.mkdir()
    _write(active, "a1", keywords=["a", "b", "c", "d", "e", "f", "g"], actions=["x"], status="active")
    _write(shadow, "s1", keywords=["a", "b", "c", "d", "e", "f"], actions=["x"])  # 重叠6 dup
    r = tier_shadow_cards(active, shadow)
    dup = [c for c in r.high_dup if c.id == "s1"][0]
    assert "6" in dup.reason  # 含重叠数值
