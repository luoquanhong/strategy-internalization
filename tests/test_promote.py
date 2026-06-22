"""C(P7): shadow 卡晋升评估器 TDD 测试。

6 指标（GPT-5.5 定），其中5个可自动判，1个留人工：
1. 有命中次数：hit_count > 0（证明真的被触发过）
2. 覆盖 active 盲点：与所有 active 最大重叠 ≤3（补了现有卡的缺口）
3. 与 active 不冲突：与所有 active 最大重叠 <6（不是重复卡）
4. 表达简洁：3 ≤ len(actions) ≤ 5（不啰嗦也不空洞）
5. 可执行性：action 具体性 ≥0.5
6. 失败修复能力：留人工（无法自动判）

晋升候选门槛：5 个自动指标中 ≥4 满足 → promote_ready=True。
"""
import yaml
from strategy_internalization.promote import evaluate_promotion, PromoteVerdict


def _write(d, cid, keywords, actions, status="shadow"):
    (d / f"{cid}.yaml").write_text(yaml.safe_dump({
        "id": cid, "title": cid, "scenario_tags": ["system_design"],
        "trigger_keywords": keywords, "actions": actions,
        "priority": 5, "status": status,
    }, allow_unicode=True, sort_keys=False))


def test_has_hits_indicator(tmp_path):
    """指标1：有命中 → True。"""
    shadow = tmp_path / "shadow"; shadow.mkdir()
    active = tmp_path / "active"; active.mkdir()
    _write(shadow, "s1", keywords=["a","b","c","d","e"], actions=["运行基准测试记录延迟数值"])
    r = evaluate_promotion(shadow, active, hits={"s1": 3})
    v = [x for x in r if x.id == "s1"][0]
    assert v.metrics["has_hits"] is True


def test_blind_spot_coverage_indicator(tmp_path):
    """指标2：与 active 最大重叠 ≤3 → True（补了盲点）。"""
    shadow = tmp_path / "shadow"; shadow.mkdir()
    active = tmp_path / "active"; active.mkdir()
    _write(active, "a1", keywords=["x","y","z"], actions=["x"], status="active")
    _write(shadow, "s1", keywords=["a","b","c"], actions=["运行基准测试记录延迟数值"])  # 重叠0
    r = evaluate_promotion(shadow, active)
    v = [x for x in r if x.id == "s1"][0]
    assert v.metrics["blind_spot_coverage"] is True


def test_no_conflict_indicator(tmp_path):
    """指标3：与 active 重叠 <6 → True。"""
    shadow = tmp_path / "shadow"; shadow.mkdir()
    active = tmp_path / "active"; active.mkdir()
    _write(active, "a1", keywords=["a","b"], actions=["x"], status="active")
    _write(shadow, "s1", keywords=["a","b","c","d","e"], actions=["运行基准测试记录延迟数值"])
    r = evaluate_promotion(shadow, active)
    v = [x for x in r if x.id == "s1"][0]
    assert v.metrics["no_conflict"] is True


def test_concise_expression_indicator(tmp_path):
    """指标4：actions 3-5 条 → True。"""
    shadow = tmp_path / "shadow"; shadow.mkdir()
    active = tmp_path / "active"; active.mkdir()
    _write(shadow, "s1", keywords=["a","b","c","d","e"],
           actions=["运行基准测试记录延迟数值","写pytest覆盖边界","拆函数加结构化日志"])
    r = evaluate_promotion(shadow, active)
    v = [x for x in r if x.id == "s1"][0]
    assert v.metrics["concise"] is True


def test_too_many_actions_not_concise(tmp_path):
    """指标4边界：actions >5 → 不简洁。"""
    shadow = tmp_path / "shadow"; shadow.mkdir()
    active = tmp_path / "active"; active.mkdir()
    _write(shadow, "s1", keywords=["a","b","c","d","e"],
           actions=[f"运行基准测试记录延迟数值{i}" for i in range(7)])
    r = evaluate_promotion(shadow, active)
    v = [x for x in r if x.id == "s1"][0]
    assert v.metrics["concise"] is False


def test_executable_indicator(tmp_path):
    """指标5：action 具体性 ≥0.5 → True。"""
    shadow = tmp_path / "shadow"; shadow.mkdir()
    active = tmp_path / "active"; active.mkdir()
    _write(shadow, "s1", keywords=["a","b","c","d","e"],
           actions=["运行基准测试记录延迟数值","写pytest覆盖边界","拆函数加结构化日志"])
    r = evaluate_promotion(shadow, active)
    v = [x for x in r if x.id == "s1"][0]
    assert v.metrics["executable"] is True


def test_promote_ready_when_4_of_5(tmp_path):
    """5 自动指标满足4个 → promote_ready=True。"""
    shadow = tmp_path / "shadow"; shadow.mkdir()
    active = tmp_path / "active"; active.mkdir()
    _write(active, "a1", keywords=["x","y"], actions=["x"], status="active")
    _write(shadow, "s1",
           keywords=["a","b","c","d","e"],  # 盲点✓ 不冲突✓
           actions=["运行基准测试记录延迟数值","写pytest覆盖边界","拆函数加结构化日志"])  # 简洁✓ 可执行✓
    r = evaluate_promotion(shadow, active, hits={"s1": 5})  # 有命中✓
    v = [x for x in r if x.id == "s1"][0]
    assert v.promote_ready is True


def test_not_ready_when_only_3(tmp_path):
    """只满足3个 → promote_ready=False（需≥4）。"""
    shadow = tmp_path / "shadow"; shadow.mkdir()
    active = tmp_path / "active"; active.mkdir()
    _write(active, "a1", keywords=["x","y"], actions=["x"], status="active")
    _write(shadow, "s1",
           keywords=["a","b","c","d","e"],  # 盲点✓ 不冲突✓
           actions=["只有两条动作不够简洁","第二条具体动作一二三"])  # 简洁✗(2条) 可执行✓
    r = evaluate_promotion(shadow, active, hits={})  # 无命中✗
    v = [x for x in r if x.id == "s1"][0]
    # 满足: 盲点✓ 不冲突✓ 可执行✓ = 3个 <4 → 不 ready
    assert v.promote_ready is False


def test_manual_indicator_listed(tmp_path):
    """指标6（失败修复能力）标注为 manual，不参与自动计分。"""
    shadow = tmp_path / "shadow"; shadow.mkdir()
    active = tmp_path / "active"; active.mkdir()
    _write(shadow, "s1", keywords=["a","b","c","d","e"], actions=["运行基准测试记录延迟数值"])
    r = evaluate_promotion(shadow, active)
    v = [x for x in r if x.id == "s1"][0]
    assert "failure_fix" in v.manual_indicators
