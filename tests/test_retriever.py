"""
TDD RED 阶段测试 — 策略内化层 (strategy_internalization)
测试合约依据 SPEC Phase 0，覆盖要点 1~8
"""
import os
import json
import time
import pytest
import yaml
from pathlib import Path
from strategy_internalization.models import StrategyCard, TaskSignals, StrategyPacket
from strategy_internalization.retriever import (
    load_active_cards,
    score_card,
    retrieve,
    estimate_tokens,
    compile_packet,
)


# ---------- helpers ----------
def _write_card(cards_dir: Path, card_id: str, *, filename=None, **overrides) -> StrategyCard:
    """将策略卡片写入 cards_dir/{card_id}.yaml 并返回对应的 StrategyCard 对象（用于断言）"""
    defaults = {
        "id": card_id,
        "title": f"Card {card_id}",
        "scenario_tags": ["general"],
        "trigger_keywords": ["test"],
        "actions": ["action1", "action2"],
        "priority": 5,
        "status": "active",
        "source": None,
    }
    data = {**defaults, **overrides}
    if filename:
        file_path = cards_dir / filename
    else:
        file_path = cards_dir / f"{card_id}.yaml"
    os.makedirs(cards_dir, exist_ok=True)
    with open(file_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False)
    # 返回对应的 StrategyCard 以便在测试中使用
    return StrategyCard(
        id=data["id"],
        title=data["title"],
        scenario_tags=data["scenario_tags"],
        trigger_keywords=data["trigger_keywords"],
        actions=data["actions"],
        priority=data["priority"],
        status=data["status"],
        source=data.get("source"),
    )


# ===============================
# 测试 load_active_cards（要点1）
# ===============================
def test_load_only_active(tmp_path):
    """覆盖要点1：只加载 active，跳过 shadow/archived"""
    cards_dir = tmp_path / "cards"
    active_card = _write_card(cards_dir, "card_active", status="active")
    _write_card(cards_dir, "card_shadow", status="shadow")
    _write_card(cards_dir, "card_archived", status="archived")
    loaded = load_active_cards(str(cards_dir))
    ids = {c.id for c in loaded}
    assert ids == {active_card.id}
    assert all(c.status == "active" for c in loaded)

def test_load_ignores_shadow_subdir(tmp_path):
    """覆盖要点1：忽略 cards/shadow/ 子目录下的卡片"""
    cards_dir = tmp_path / "cards"
    shadow_dir = cards_dir / "shadow"
    os.makedirs(shadow_dir)
    _write_card(cards_dir, "root_active", status="active")
    # 写在 shadow/ 子目录下
    _write_card(shadow_dir, "hidden_in_shadow", status="active")
    loaded = load_active_cards(str(cards_dir))
    ids = {c.id for c in loaded}
    assert ids == {"root_active"}

def test_load_duplicate_id_raises(tmp_path):
    """覆盖要点1：id 重复时抛出 ValueError"""
    cards_dir = tmp_path / "cards"
    # 两个不同文件，但 YAML 内容中的 id 相同
    _write_card(cards_dir, "dup", filename="dup1.yaml", title="One")
    _write_card(cards_dir, "dup", filename="dup2.yaml", title="Two")
    with pytest.raises(ValueError):
        load_active_cards(str(cards_dir))

# ===============================
# 测试 score_card（要点2/8）
# ===============================
def test_score_card_exact_scenario_hit():
    """覆盖要点2：显式场景命中 +0.30"""
    card = StrategyCard("c1", "T", ["ops_config"], ["param"], ["act"], priority=5, status="active")
    signals = TaskSignals(scenario="ops_config", keywords=["unrelated"], text="task")
    expected = 0.30 + 0.0 + 0.0 + (5 / 10) * 0.20  # 0.30 + 0.0 + 0.0 + 0.10 = 0.40
    assert score_card(card, signals) == pytest.approx(0.40)

def test_score_card_tag_hit():
    """覆盖要点2：scenario_tags 关键词命中 (B part)"""
    card = StrategyCard("c2", "T", ["bug_fix", "refactor"], [], ["act"], priority=5, status="active")
    # keywords 里包含了标签的字符串
    signals = TaskSignals(scenario=None, keywords=["bug_fix", "else"], text="task")
    # B: tag_hits = 1 (bug_fix) -> +0.10; C: 0; D: 0.10 -> total 0.20
    assert score_card(card, signals) == pytest.approx(0.20)

def test_score_card_keyword_hit():
    """覆盖要点2：trigger_keywords 命中"""
    card = StrategyCard("c3", "T", ["new_build"], ["abc", "xyz"], ["act"], priority=10, status="active")
    signals = TaskSignals(scenario=None, keywords=["abc", "xyz", "other"], text="task")
    # A: 0; B: 0; C: kw_hits=2 -> 0.40; D: (10/10)*0.20=0.20 -> total 0.60
    assert score_card(card, signals) == pytest.approx(0.60)

def test_score_card_priority_baseline():
    """覆盖要点2：零命中时仍有 priority 基底分"""
    card = StrategyCard("c4", "T", ["general"], ["x"], ["y"], priority=1, status="active")
    signals = TaskSignals(scenario=None, keywords=["something"], text="task")
    # 完全无命中，只有 D: (1/10)*0.20=0.02
    assert score_card(card, signals) == pytest.approx(0.02)

def test_score_card_normalization():
    """覆盖要点2：得分不超过 1.0"""
    card = StrategyCard("c5", "T", ["a","b","c","d","e"], ["k1","k2","k3","k4","k5","k6"], ["act"], priority=10, status="active")
    signals = TaskSignals(scenario="a", keywords=["a","b","c","k1","k2","k3","k4","k5","k6"], text="t")
    # A: +0.30; B: tag_hits: keywords 中 "a","b","c" 命中 scenario_tags -> len=3 -> +0.30; C: all 6 keywords hit -> 6*0.20=1.20; D: 0.20 -> sum=2.00, capped 1.0
    assert score_card(card, signals) == pytest.approx(1.0)

def test_score_card_deterministic():
    """覆盖要点8：相同输入多次调用结果一致"""
    card = StrategyCard("c6", "T", ["ops_config"], ["param"], ["check"], priority=7, status="active")
    signals = TaskSignals(scenario="ops_config", keywords=["param", "setup"], text="task")
    result1 = score_card(card, signals)
    result2 = score_card(card, signals)
    assert result1 == result2

# ===============================
# 测试 retrieve 正常路径（要点3）
# ===============================
def test_retrieve_normal(tmp_path):
    """覆盖要点3：正常返回 ≤3 张，按分降序，top_scores 与 cards 对应，retrieved=True"""
    cards_dir = tmp_path / "cards"
    # 造三张卡片，得分可预测：人为控制 signals
    c1 = _write_card(cards_dir, "c1", scenario_tags=["ops_config"], trigger_keywords=["param"],
                     actions=["a"], priority=10, status="active")
    c2 = _write_card(cards_dir, "c2", scenario_tags=["ops_config"], trigger_keywords=["config"],
                     actions=["b"], priority=8, status="active")
    c3 = _write_card(cards_dir, "c3", scenario_tags=["new_build"], trigger_keywords=["create"],
                     actions=["c"], priority=5, status="active")
    # 使用 scenario="ops_config", keywords=["param","config"] 使 c1,c2 高分，c3 低分
    signals = TaskSignals(scenario="ops_config", keywords=["param", "config"], text="set params")
    result = retrieve(signals, cards_dir=str(cards_dir),
                      state_file=str(tmp_path / "state.json"), request_id="req1")
    assert isinstance(result, StrategyPacket)
    assert result.retrieved is True
    assert not result.degraded
    assert len(result.cards) <= 3
    # c1 应得分高于 c2，c3 最低（可能被 max_cards 截断，这里刚好 3 张全选）
    assert len(result.cards) == 2
    # 验证降序：只保留达到相关性阈值的 c1/c2，低分 c3 不再硬塞
    scores = [score_card(c, signals) for c in [c1, c2, c3]]
    expected_order_ids = [c.id for score, c in sorted(zip(scores, [c1, c2, c3]), key=lambda x: x[0], reverse=True) if score >= 0.3]
    assert [c.id for c in result.cards] == expected_order_ids
    # top_scores 应与 cards 对应，且均达到相关性阈值
    assert result.top_scores == [score_card(c, signals) for c in result.cards]
    assert all(score >= 0.3 for score in result.top_scores)

# ===============================
# 测试 retrieve 降级（要点4）
# ===============================
def test_retrieve_degraded(tmp_path):
    """覆盖要点4：最高分低于 degrade_threshold 时降级，degraded=True，选中 general cards"""
    cards_dir = tmp_path / "cards"
    # 一张不匹配任何信号的卡片，最高分很低
    _write_card(cards_dir, "low", scenario_tags=["security_sanitization"],
                trigger_keywords=["encrypt"], priority=1, status="active")
    gen1 = _write_card(cards_dir, "fallback1", scenario_tags=["general"],
                       trigger_keywords=["generic"], priority=10, status="active")
    gen2 = _write_card(cards_dir, "fallback2", scenario_tags=["general"],
                       trigger_keywords=["generic"], priority=8, status="active")
    gen3 = _write_card(cards_dir, "fallback3", scenario_tags=["general"],
                       trigger_keywords=["generic"], priority=5, status="active")
    # signals 刻意不匹配任何卡
    signals = TaskSignals(scenario=None, keywords=["xyz"], text="nothing matches")
    result = retrieve(signals, cards_dir=str(cards_dir),
                      state_file=str(tmp_path / "state.json"), request_id="req_d",
                      degrade_threshold=0.3, top_n_for_degrade_fallback=3)
    assert result.degraded is True
    # 应该只包含 general cards ，按 priority 降序取前3
    assert len(result.cards) == 3
    assert all("general" in c.scenario_tags for c in result.cards)
    # 排序：priority 降序
    priorities = [c.priority for c in result.cards]
    assert priorities == sorted(priorities, reverse=True)
    # 补充 top_scores 长度一致
    assert len(result.top_scores) == len(result.cards)

# 降级且 general card 数量不足
def test_retrieve_degraded_few_generals(tmp_path):
    """覆盖要点4（补充）：general 卡片不足 top_n 时取全部"""
    cards_dir = tmp_path / "cards"
    _write_card(cards_dir, "low", scenario_tags=["bug_fix"], trigger_keywords=["fix"], priority=1, status="active")
    _write_card(cards_dir, "gen_only", scenario_tags=["general"], trigger_keywords=["g"], priority=7, status="active")
    signals = TaskSignals(scenario=None, keywords=["xyz"], text="task")
    result = retrieve(signals, cards_dir=str(cards_dir),
                      state_file=str(tmp_path / "state.json"), request_id="r2",
                      degrade_threshold=0.3)
    assert result.degraded is True
    assert len(result.cards) == 1
    assert result.cards[0].id == "gen_only"

# ===============================
# 测试 token 闸门（要点5）
# ===============================
def test_retrieve_token_limit(tmp_path):
    """覆盖要点5：累计 token 超 max_tokens 时截断，packet.tokens <= 800"""
    cards_dir = tmp_path / "cards"
    # 创建一张高分小卡，一张低分大卡（长 actions），确保小卡先入选
    small = _write_card(cards_dir, "small", scenario_tags=["ops_config"],
                        trigger_keywords=["t"], actions=["a"], priority=10, status="active")
    large = _write_card(cards_dir, "large", scenario_tags=["general"],
                        trigger_keywords=["t"], actions=["x" * 300] * 10, priority=1, status="active")
    # signals 匹配 ops_config 场景，使 small 得分远高于 large
    signals = TaskSignals(scenario="ops_config", keywords=["t"], text="task")
    result = retrieve(signals, cards_dir=str(cards_dir),
                      state_file=str(tmp_path / "state.json"), request_id="req_tok",
                      max_tokens=800)
    assert len(result.cards) == 1  # 只有小卡入选
    assert result.cards[0].id == small.id
    # 确认 packet 文本 token 估算 <= 800
    est_tokens = estimate_tokens(result.text)
    assert result.tokens == est_tokens
    assert result.tokens <= 800

# 第一张卡本身就超 token 也可能被丢弃，验证 cards 为空时 tokens 处理
def test_retrieve_token_limit_first_card_oversized(tmp_path):
    """要点5补充：第一张卡片若超过 max_tokens 则丢弃，结果 cards 为空"""
    cards_dir = tmp_path / "cards"
    # 只有一张超大 card
    _write_card(cards_dir, "big", scenario_tags=["ops_config"],
                trigger_keywords=["t"], actions=["x" * 600] * 3, priority=10, status="active")
    signals = TaskSignals(scenario="ops_config", keywords=["t"], text="task")
    result = retrieve(signals, cards_dir=str(cards_dir),
                      state_file=str(tmp_path / "state.json"), request_id="req_big",
                      max_tokens=50)  # 极小阈值
    assert len(result.cards) == 0
    assert result.tokens == estimate_tokens(result.text)

# ===============================
# 测试状态外置（要点6）
# ===============================
def test_retrieve_state_caching_same_request_id(tmp_path):
    """覆盖要点6：同 request_id 第二次调用 retrieved=False 且内容一致（ids、text、degraded）"""
    cards_dir = tmp_path / "cards"
    _write_card(cards_dir, "card1", scenario_tags=["ops_config"], trigger_keywords=["a"],
                actions=["do"], priority=5, status="active")
    signals = TaskSignals(scenario="ops_config", keywords=["a"], text="task")
    state_path = tmp_path / "state.json"
    first = retrieve(signals, cards_dir=str(cards_dir),
                     state_file=str(state_path), request_id="reqA")
    assert first.retrieved is True
    second = retrieve(signals, cards_dir=str(cards_dir),
                      state_file=str(state_path), request_id="reqA")
    assert second.retrieved is False
    # 卡片 id 顺序一致
    assert [c.id for c in second.cards] == [c.id for c in first.cards]
    # packet 文本一致
    assert second.text == first.text
    # 降级标志一致
    assert second.degraded == first.degraded

def test_retrieve_state_isolation_different_request_ids(tmp_path):
    """覆盖要点6补充：不同 request_id 各自检索，互不影响"""
    cards_dir = tmp_path / "cards"
    c1 = _write_card(cards_dir, "c1", scenario_tags=["ops_config"], trigger_keywords=["a"],
                     actions=["go"], priority=8, status="active")
    _write_card(cards_dir, "c2", scenario_tags=["new_build"], trigger_keywords=["b"],
                actions=["build"], priority=5, status="active")
    state_path = tmp_path / "state.json"
    signals = TaskSignals(scenario="ops_config", keywords=["a"], text="first")
    first = retrieve(signals, cards_dir=str(cards_dir),
                     state_file=str(state_path), request_id="user1")
    assert first.retrieved is True
    # 第二次使用不同 request_id
    second = retrieve(signals, cards_dir=str(cards_dir),
                      state_file=str(state_path), request_id="user2")
    assert second.retrieved is True  # 应真正检索
    # 同时验证状态文件中包含两个记录
    with open(state_path) as f:
        state = json.load(f)
    assert "user1" in state
    assert "user2" in state

# ===============================
# 测试空库（要点7）
# ===============================
def test_retrieve_empty_cards(tmp_path):
    """覆盖要点7：cards 目录无 active 卡片时返回空 packet，retrieved=True"""
    cards_dir = tmp_path / "cards"
    os.makedirs(cards_dir, exist_ok=True)  # 空目录，无任何 yaml
    signals = TaskSignals(scenario="ops_config", keywords=["a"], text="task")
    result = retrieve(signals, cards_dir=str(cards_dir),
                      state_file=str(tmp_path / "state.json"), request_id="empty")
    assert result.retrieved is True
    assert not result.degraded
    assert len(result.cards) == 0
    assert result.tokens == estimate_tokens(result.text)
    assert result.tokens <= 800
    # 状态文件应记录
    with open(tmp_path / "state.json") as f:
        state = json.load(f)
    assert state["empty"]["cards_ids"] == []

# ===============================
# 测试 compile_packet 格式（间接验证）
# ===============================
def test_compile_packet_format():
    """验证 packet 文本包含预期元素（确保格式正确）"""
    cards = [
        StrategyCard("c1", "超级策略", ["ops_config"], ["param"], ["检查配置", "更新依赖"], priority=8, status="active"),
    ]
    text = compile_packet(cards)
    assert "任务相关策略卡" in text
    assert "### 1. 超级策略" in text
    assert "适用: ops_config" in text
    assert "- 检查配置" in text
    assert "- 更新依赖" in text
    assert "优先级: P8" in text

# ===============================
# 测试 estimate_tokens 基础
# ===============================
def test_estimate_tokens():
    assert estimate_tokens("hello") == int(len("hello") / 1.5) + 1
    assert estimate_tokens("") == 1


# ===============================
# 回归测试：N3/N4 降级兜底必须可达
# ===============================
def test_retrieve_degrades_when_top_score_equals_threshold(tmp_path):
    """N3/N4: top_score == degrade_threshold 时也应降级到 general 卡。

    当前 bug：score_card 的 scenario A 分支正好 +0.30，而 retrieve 用
    `top_score < degrade_threshold` 判断降级，导致 0.30 边界不降级，
    general fallback 卡不可达。
    """
    cards_dir = tmp_path / "cards"
    review_card = _write_card(
        cards_dir,
        "review-boundary",
        scenario_tags=["review"],
        trigger_keywords=["深度审查"],
        actions=["具体审查动作"],
        priority=0,
        status="active",
    )
    general_card = _write_card(
        cards_dir,
        "general-pragmatic",
        scenario_tags=["general"],
        trigger_keywords=[],
        actions=["通用验证闭环"],
        priority=0,
        status="active",
    )
    signals = TaskSignals(scenario="review", keywords=["完全无关词"], text="低置信任务")

    assert score_card(review_card, signals) == pytest.approx(0.30)
    assert score_card(general_card, signals) < 0.30

    result = retrieve(
        signals,
        cards_dir=str(cards_dir),
        state_file=str(tmp_path / "state.json"),
        request_id="boundary-equals-threshold",
        degrade_threshold=0.3,
    )

    assert result.degraded is True, "top_score == degrade_threshold 应视为低置信并降级"
    assert result.cards
    assert result.cards[0].id == general_card.id


def test_retrieve_keeps_high_confidence_specific_match(tmp_path):
    """高置信具体匹配不应因 N3 修复被误降级。"""
    cards_dir = tmp_path / "cards"
    review_card = _write_card(
        cards_dir,
        "review-high-confidence",
        scenario_tags=["review"],
        trigger_keywords=["review"],
        actions=["具体审查动作"],
        priority=10,
        status="active",
    )
    _write_card(
        cards_dir,
        "general-pragmatic",
        scenario_tags=["general"],
        trigger_keywords=[],
        actions=["通用验证闭环"],
        priority=0,
        status="active",
    )
    signals = TaskSignals(scenario="review", keywords=["review"], text="需要审查")

    assert score_card(review_card, signals) > 0.3

    result = retrieve(
        signals,
        cards_dir=str(cards_dir),
        state_file=str(tmp_path / "state.json"),
        request_id="high-confidence",
        degrade_threshold=0.3,
    )

    assert result.degraded is False
    assert result.cards
    assert result.cards[0].id == review_card.id


def test_retrieve_degrades_when_top_score_below_threshold_to_general(tmp_path):
    """保护原有行为：top_score < threshold 时仍降级到 general fallback。"""
    cards_dir = tmp_path / "cards"
    _write_card(
        cards_dir,
        "specific-low",
        scenario_tags=["ops_config"],
        trigger_keywords=["配置"],
        actions=["具体配置动作"],
        priority=0,
        status="active",
    )
    general_card = _write_card(
        cards_dir,
        "general-pragmatic",
        scenario_tags=["general"],
        trigger_keywords=[],
        actions=["通用验证闭环"],
        priority=10,
        status="active",
    )
    signals = TaskSignals(scenario="review", keywords=["无关词"], text="低置信任务")

    top_score = max(score_card(c, signals) for c in load_active_cards(str(cards_dir)))
    assert top_score < 0.3

    result = retrieve(
        signals,
        cards_dir=str(cards_dir),
        state_file=str(tmp_path / "state.json"),
        request_id="below-threshold",
        degrade_threshold=0.3,
    )

    assert result.degraded is True
    assert result.cards
    assert result.cards[0].id == general_card.id


# ===============================
# 回归测试：N7 state 清理机制
# ===============================
def test_retrieve_evicts_expired_state_entries(tmp_path):
    """N7: 超过 ttl_seconds 的 state 条目应被清理，state 文件不再无限增长。"""
    cards_dir = tmp_path / "cards"
    _write_card(
        cards_dir,
        "c1",
        scenario_tags=["ops_config"],
        trigger_keywords=["a"],
        actions=["do"],
        priority=5,
        status="active",
    )
    state_path = tmp_path / "state.json"
    # 预置一条过期的旧条目（created_at 指向 10 天前）
    old_ts = time.time() - 10 * 86400
    stale = {
        "old-request": {
            "cards": [],
            "cards_ids": [],
            "text": "",
            "tokens": 1,
            "degraded": False,
            "retrieved": True,
            "top_scores": [],
            "created_at": old_ts,
        }
    }
    with open(state_path, "w") as f:
        json.dump(stale, f)

    signals = TaskSignals(scenario="ops_config", keywords=["a"], text="task")
    retrieve(
        signals,
        cards_dir=str(cards_dir),
        state_file=str(state_path),
        request_id="new-request",
        ttl_seconds=86400,
    )

    with open(state_path) as f:
        state = json.load(f)
    assert "old-request" not in state, "过期条目应被清理"
    assert "new-request" in state


def test_retrieve_preserves_fresh_state_entries(tmp_path):
    """N7: 未过期的条目应保留，不被误清理。"""
    cards_dir = tmp_path / "cards"
    _write_card(
        cards_dir,
        "c1",
        scenario_tags=["ops_config"],
        trigger_keywords=["a"],
        actions=["do"],
        priority=5,
        status="active",
    )
    state_path = tmp_path / "state.json"
    fresh_ts = time.time() - 60  # 1 分钟前，未过期
    fresh = {
        "fresh-request": {
            "cards": [],
            "cards_ids": [],
            "text": "",
            "tokens": 1,
            "degraded": False,
            "retrieved": True,
            "top_scores": [],
            "created_at": fresh_ts,
        }
    }
    with open(state_path, "w") as f:
        json.dump(fresh, f)

    signals = TaskSignals(scenario="ops_config", keywords=["a"], text="task")
    retrieve(
        signals,
        cards_dir=str(cards_dir),
        state_file=str(state_path),
        request_id="new-request",
        ttl_seconds=86400,
    )

    with open(state_path) as f:
        state = json.load(f)
    assert "fresh-request" in state, "未过期条目应保留"
    assert "new-request" in state


# ===============================
# 回归测试：N5 不硬塞低分无关卡
# ===============================
def test_retrieve_drops_low_relevance_cards_below_min(tmp_path):
    """N5: 只有1张相关卡时，不应硬塞 priority-only 低分卡。

    当前 bug：retrieve 正常路径只取 scored[:max_cards]，没有最低相关性
    过滤。低分卡靠 priority 得 0.16~0.18 仍会被塞进 packet 稀释主卡。
    """
    cards_dir = tmp_path / "cards"
    high_card = _write_card(
        cards_dir,
        "refactor-perf",
        scenario_tags=["refactor"],
        trigger_keywords=["性能"],
        actions=["量化瓶颈"],
        priority=10,
        status="active",
    )
    low_card_1 = _write_card(
        cards_dir,
        "ops-irrelevant",
        scenario_tags=["ops_config"],
        trigger_keywords=["不相关"],
        actions=["无关动作"],
        priority=8,
        status="active",
    )
    low_card_2 = _write_card(
        cards_dir,
        "new-build-irrelevant",
        scenario_tags=["new_build"],
        trigger_keywords=["不相关"],
        actions=["无关动作"],
        priority=7,
        status="active",
    )
    signals = TaskSignals(scenario="refactor", keywords=["性能"], text="优化性能")

    high_score = score_card(high_card, signals)
    low1_score = score_card(low_card_1, signals)
    low2_score = score_card(low_card_2, signals)
    assert high_score > 0.3
    assert low1_score < 0.3
    assert low2_score < 0.3

    result = retrieve(
        signals,
        cards_dir=str(cards_dir),
        state_file=str(tmp_path / "state.json"),
        request_id="n5-drop-low",
        degrade_threshold=0.3,
        max_cards=3,
    )

    assert result.degraded is False
    selected_ids = [c.id for c in result.cards]
    assert "refactor-perf" in selected_ids
    assert "ops-irrelevant" not in selected_ids, "低分无关卡不应被硬塞"
    assert "new-build-irrelevant" not in selected_ids, "低分无关卡不应被硬塞"
    for s in result.top_scores:
        assert s >= 0.3, f"低分卡 {s} 不应进入 packet"


def test_retrieve_keeps_all_three_when_all_relevant(tmp_path):
    """保护测试：三张卡都相关（分数 >= degrade_threshold）时仍返回 max_cards 张。"""
    cards_dir = tmp_path / "cards"
    for cid, prio in [("refactor-a", 10), ("refactor-b", 9), ("refactor-c", 8)]:
        _write_card(
            cards_dir,
            cid,
            scenario_tags=["refactor"],
            trigger_keywords=["性能"],
            actions=["量化瓶颈"],
            priority=prio,
            status="active",
        )
    signals = TaskSignals(scenario="refactor", keywords=["性能"], text="优化性能")

    result = retrieve(
        signals,
        cards_dir=str(cards_dir),
        state_file=str(tmp_path / "state.json"),
        request_id="n5-all-relevant",
        degrade_threshold=0.3,
        max_cards=3,
    )

    assert result.degraded is False
    assert len(result.cards) == 3
    for s in result.top_scores:
        assert s >= 0.3


# ===============================
# 实仓 cards 回归：系统设计/架构隐患不能只降级到 general
# ===============================
def test_real_cards_system_design_architecture_risks_retrieve_specific_cards(tmp_path):
    """架构隐患任务应命中 system_design 专项 active 卡，而不是只拿 general 兜底。"""
    repo_root = Path(__file__).resolve().parents[1]
    signals = TaskSignals(
        scenario="system_design",
        keywords=["架构", "隐患", "控制平面", "推理平面", "死循环", "硬闸门"],
        text="继续处理策略内化层架构隐患",
    )
    result = retrieve(
        signals,
        cards_dir=str(repo_root / "cards"),
        state_file=str(tmp_path / "state.json"),
        request_id="arch_risk_real_cards",
    )
    assert result.degraded is False
    assert result.cards, "必须返回至少一张专项策略卡"
    assert all("general" not in c.scenario_tags for c in result.cards), "不能只降级到 general 兜底卡"
    assert any("system_design" in c.scenario_tags for c in result.cards)


def test_real_cards_declared_active_keywords_are_extractable():
    """实仓 active 卡声明的非 general 关键词必须能被 signal_extractor 提取。"""
    from strategy_internalization.signal_extractor import _get_scenario_keywords, rebuild_scenario_keywords

    repo_root = Path(__file__).resolve().parents[1]
    rebuild_scenario_keywords()
    kw = _get_scenario_keywords(str(repo_root / "cards"))
    dict_words = set()
    for words in kw.values():
        dict_words.update(w.lower() for w in words)
    dead = []
    for card in load_active_cards(str(repo_root / "cards")):
        if "general" in card.scenario_tags:
            continue
        for trigger_kw in card.trigger_keywords:
            if trigger_kw.lower() not in dict_words:
                dead.append((card.id, trigger_kw))
    assert dead == []


def test_retrieve_treats_empty_state_file_as_empty_state(tmp_path):
    """状态文件存在但为空时，不应崩溃，应按空状态继续检索。"""
    cards_dir = tmp_path / "cards"
    _write_card(cards_dir, "card1", scenario_tags=["system_design"], trigger_keywords=["架构"], actions=["act"], priority=8, status="active")
    state_path = tmp_path / "state.json"
    state_path.write_text("")
    signals = TaskSignals(scenario="system_design", keywords=["架构"], text="架构隐患")
    result = retrieve(signals, cards_dir=str(cards_dir), state_file=str(state_path), request_id="empty_state")
    assert result.retrieved is True
    assert [c.id for c in result.cards] == ["card1"]


def test_retrieve_treats_corrupt_state_file_as_empty_state(tmp_path):
    """状态文件损坏时，不应让检索链路整体崩溃，应按空状态自愈重写。"""
    cards_dir = tmp_path / "cards"
    _write_card(cards_dir, "card1", scenario_tags=["system_design"], trigger_keywords=["架构"], actions=["act"], priority=8, status="active")
    state_path = tmp_path / "state.json"
    state_path.write_text("not-json")
    signals = TaskSignals(scenario="system_design", keywords=["架构"], text="架构隐患")
    result = retrieve(signals, cards_dir=str(cards_dir), state_file=str(state_path), request_id="corrupt_state")
    assert result.retrieved is True
    assert [c.id for c in result.cards] == ["card1"]
    assert "corrupt_state" in json.loads(state_path.read_text())
