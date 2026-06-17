"""P0-2 保守注入 TDD 测试（GPT-5.5 评审定稿）。

需求：
- max_cards 默认 2（不是 3）
- max_tokens 默认 300（不是 800）
- 置信度决定卡数：高置信(top1>=high_confidence_threshold)最多 max_cards 张；
  中置信(degrade_threshold<top1<high_confidence_threshold)只注入 1 张（单卡模式，防多卡叠加冲突）
"""
import os
import inspect
import yaml
import pytest
from pathlib import Path
from strategy_internalization.models import TaskSignals
from strategy_internalization.retriever import retrieve


def _write_card(cards_dir: Path, card_id: str, **overrides):
    defaults = {
        "id": card_id,
        "title": f"Card {card_id}",
        "scenario_tags": ["general"],
        "trigger_keywords": ["test"],
        "actions": ["action1"],
        "priority": 5,
        "status": "active",
        "source": None,
    }
    data = {**defaults, **overrides}
    os.makedirs(cards_dir, exist_ok=True)
    with open(cards_dir / f"{card_id}.yaml", "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


def test_conservative_defaults_in_signature():
    """签名默认值：max_cards=2, max_tokens=300, high_confidence_threshold=0.5。"""
    sig = inspect.signature(retrieve)
    assert sig.parameters["max_cards"].default == 2
    assert sig.parameters["max_tokens"].default == 300
    assert sig.parameters["high_confidence_threshold"].default == 0.5


def test_high_confidence_returns_up_to_max_cards(tmp_path):
    """高置信(top1>=0.5)：返回最多 max_cards 张。"""
    cards_dir = tmp_path / "cards"
    # 两张都强匹配 refactor + 性能：scenario0.30 + kw0.20 + prio
    _write_card(cards_dir, "perf-a", scenario_tags=["refactor"],
                trigger_keywords=["性能"], actions=["量化瓶颈"], priority=10)
    _write_card(cards_dir, "perf-b", scenario_tags=["refactor"],
                trigger_keywords=["性能"], actions=["量化瓶颈"], priority=9)
    signals = TaskSignals(scenario="refactor", keywords=["性能"], text="优化性能")
    result = retrieve(signals, cards_dir=str(cards_dir),
                      state_file=str(tmp_path / "s.json"), request_id="hc")
    assert result.degraded is False
    assert result.top_scores[0] >= 0.5
    assert len(result.cards) == 2  # 默认 max_cards=2


def test_medium_confidence_returns_single_card(tmp_path):
    """中置信(0.3<=top1<0.5)：单卡模式，只注入 1 张，防多卡叠加冲突。"""
    cards_dir = tmp_path / "cards"
    # scenario 命中但无关键词命中 + 中等 priority → top1 落在 [0.3,0.5)
    _write_card(cards_dir, "mid-a", scenario_tags=["ops_config"],
                trigger_keywords=["不会命中"], actions=["动作a"], priority=5)
    _write_card(cards_dir, "mid-b", scenario_tags=["ops_config"],
                trigger_keywords=["不会命中"], actions=["动作b"], priority=4)
    signals = TaskSignals(scenario="ops_config", keywords=["无关词"], text="任务")
    result = retrieve(signals, cards_dir=str(cards_dir),
                      state_file=str(tmp_path / "s.json"), request_id="mc")
    assert result.degraded is False
    assert 0.3 <= result.top_scores[0] < 0.5
    assert len(result.cards) == 1, "中置信应单卡模式"


def test_token_budget_300_enforced(tmp_path):
    """token 预算默认 300：超预算的卡被截断。"""
    cards_dir = tmp_path / "cards"
    # 一张高分小卡 + 一张高分大卡（>300 token 后被截）
    _write_card(cards_dir, "small", scenario_tags=["refactor"],
                trigger_keywords=["性能"], actions=["量化瓶颈"], priority=10)
    _write_card(cards_dir, "huge", scenario_tags=["refactor"],
                trigger_keywords=["性能"], actions=["x" * 200] * 5, priority=9)
    signals = TaskSignals(scenario="refactor", keywords=["性能"], text="优化性能")
    result = retrieve(signals, cards_dir=str(cards_dir),
                      state_file=str(tmp_path / "s.json"), request_id="tb")
    assert result.tokens <= 300
    assert result.cards[0].id == "small"


def test_explicit_max_cards_3_still_honored(tmp_path):
    """显式传 max_cards=3 + 高置信仍可返回 3 张（保护现有行为）。"""
    cards_dir = tmp_path / "cards"
    for cid, prio in [("a", 10), ("b", 9), ("c", 8)]:
        _write_card(cards_dir, cid, scenario_tags=["refactor"],
                    trigger_keywords=["性能"], actions=["量化"], priority=prio)
    signals = TaskSignals(scenario="refactor", keywords=["性能"], text="优化性能")
    result = retrieve(signals, cards_dir=str(cards_dir),
                      state_file=str(tmp_path / "s.json"), request_id="m3",
                      max_cards=3, max_tokens=800)
    assert len(result.cards) == 3
