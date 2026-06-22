"""E(P4): system_design shadow 卡子类聚类 TDD 测试。

按关键词把同场景 shadow 聚成子类，帮后续精细化治理。
子类预设（GPT-5.5 建议7类）：
- boundary: 边界划分（边界/解耦/隔离/契约）
- state: 状态管理（状态/缓存/外部化）
- extensibility: 扩展性（扩展/弹性/伸缩）
- failure: 失败预案（熔断/降级/回滚/预案）
- dataflow: 数据流（反馈/闭环/管道）
- complexity: 复杂度控制（拆分/简化/最小化）
- other: 兜底
"""
import yaml
from strategy_internalization.cluster import cluster_by_subtopic, SUBTOPIC_KEYWORDS


def _card(cid, keywords, scenario="system_design"):
    return yaml.safe_dump({
        "id": cid, "title": cid, "scenario_tags": [scenario],
        "trigger_keywords": keywords, "actions": ["x"],
        "priority": 5, "status": "shadow",
    }, allow_unicode=True, sort_keys=False)


def test_card_goes_to_boundary_subtopic(tmp_path):
    """含边界/解耦关键词 → boundary 子类。"""
    d = tmp_path / "shadow"; d.mkdir()
    (d / "s1.yaml").write_text(_card("s1", ["边界", "解耦", "硬闸门"]))
    r = cluster_by_subtopic(d, scenario="system_design")
    assert "s1" in [c for c in r["boundary"]]


def test_card_goes_to_failure_subtopic(tmp_path):
    """含熔断/降级 → failure 子类。"""
    d = tmp_path / "shadow"; d.mkdir()
    (d / "s1.yaml").write_text(_card("s1", ["熔断", "降级", "回滚"]))
    r = cluster_by_subtopic(d, scenario="system_design")
    assert "s1" in r["failure"]


def test_card_with_multiple_subtopics_picks_first_match(tmp_path):
    """同时命中多个子类 → 归入第一个匹配（不重复）。"""
    d = tmp_path / "shadow"; d.mkdir()
    (d / "s1.yaml").write_text(_card("s1", ["边界", "熔断", "状态"]))
    r = cluster_by_subtopic(d, scenario="system_design")
    placed = [sub for sub, ids in r.items() if "s1" in ids]
    assert len(placed) == 1  # 恰好一类


def test_unmatched_goes_to_other(tmp_path):
    """不匹配任何子类 → other。"""
    d = tmp_path / "shadow"; d.mkdir()
    (d / "s1.yaml").write_text(_card("s1", ["量子计算", "区块链"]))
    r = cluster_by_subtopic(d, scenario="system_design")
    assert "s1" in r["other"]


def test_only_target_scenario_clustered(tmp_path):
    """只聚指定场景的卡，其他场景的忽略。"""
    d = tmp_path / "shadow"; d.mkdir()
    (d / "s1.yaml").write_text(_card("s1", ["边界"], scenario="bug_fix"))
    (d / "s2.yaml").write_text(_card("s2", ["边界"], scenario="system_design"))
    r = cluster_by_subtopic(d, scenario="system_design")
    assert "s1" not in r["boundary"]
    assert "s2" in r["boundary"]


def test_all_cards_placed_exactly_once(tmp_path):
    """每张卡恰好归一类，并集=全部目标场景卡。"""
    d = tmp_path / "shadow"; d.mkdir()
    (d / "s1.yaml").write_text(_card("s1", ["边界"]))
    (d / "s2.yaml").write_text(_card("s2", ["熔断"]))
    (d / "s3.yaml").write_text(_card("s3", ["量子"]))
    r = cluster_by_subtopic(d, scenario="system_design")
    union = set()
    for ids in r.values():
        union |= set(ids)
    assert union == {"s1", "s2", "s3"}
