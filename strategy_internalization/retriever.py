import os
import json
import time
import yaml
from pathlib import Path
from typing import Optional
from .models import StrategyCard, TaskSignals, StrategyPacket
from .tokens import estimate_tokens


def load_active_cards(cards_dir: str = "cards") -> list[StrategyCard]:
    cards_dir_path = Path(cards_dir)
    seen_ids = set()
    result = []
    for file_path in cards_dir_path.glob("*.yaml"):
        if file_path.parent.name == "shadow":
            continue
        with open(file_path) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            continue
        status = data.get("status")
        if status != "active":
            continue
        card = StrategyCard(
            id=data["id"],
            title=data.get("title", ""),
            scenario_tags=data.get("scenario_tags", []),
            trigger_keywords=data.get("trigger_keywords", []),
            actions=data.get("actions", []),
            priority=data.get("priority", 0),
            status=status,
            source=data.get("source")
        )
        if card.id in seen_ids:
            raise ValueError(f"Duplicate card id: {card.id}")
        seen_ids.add(card.id)
        result.append(card)
    return result


def score_card(card: StrategyCard, signals: TaskSignals) -> float:
    base = 0.0
    # A
    if signals.scenario is not None and signals.scenario in card.scenario_tags:
        base += 0.30
    # B
    tag_hits = len(set(signals.keywords) & set(card.scenario_tags))
    base += 0.10 * tag_hits
    # C
    kw_hits = len(set(signals.keywords) & set(card.trigger_keywords))
    base += 0.20 * kw_hits
    # D
    base += (card.priority / 10.0) * 0.20
    return min(base, 1.0)


def compile_packet(cards: list[StrategyCard]) -> str:
    inner_lines = [f"## 任务相关策略卡（{len(cards)} 张）", ""]
    for i, card in enumerate(cards, 1):
        inner_lines.append(f"### {i}. {card.title}")
        inner_lines.append(f"适用: {', '.join(card.scenario_tags)}")
        inner_lines.append("动作:")
        for action in card.actions:
            inner_lines.append(f"- {action}")
        inner_lines.append(f"优先级: P{card.priority}")
        inner_lines.append("")
    inner = "\n".join(inner_lines)
    # P0-1 强边界伪通道隔离（GPT-5.5）：空卡不输出 XML 噪音；
    # 有卡时用 XML 标签明确标记为"低优先级参考、非强制"，
    # 防止卡片被模型当成隐藏需求覆盖用户实际请求。
    if not cards:
        return inner
    return (
        "<strategy_reference priority='low' mandatory='false'>\n"
        "以下为低优先级历史经验参考，仅供借鉴，不得覆盖或凌驾用户的实际请求：\n\n"
        f"{inner}"
        "</strategy_reference>"
    )


def wrap_for_injection(packet_text: str, user_intent: str) -> str:
    """组装最终注入文本：卡片在前，用户原始请求重述放最后吃 recency。

    GPT-5.5 P0-1：把用户当前任务目标放在末尾，利用 LLM 的 recency 偏好，
    确保策略卡不喧宾夺主。空 packet（无 XML 边界）→ 返回空串，hook 据此不注入。
    """
    if "<strategy_reference" not in packet_text:
        return ""
    return f"{packet_text}\n\n当前任务唯一目标（请以此为准）: {user_intent}"


def _card_to_dict(card: StrategyCard) -> dict:
    return {
        "id": card.id,
        "title": card.title,
        "scenario_tags": card.scenario_tags,
        "trigger_keywords": card.trigger_keywords,
        "actions": card.actions,
        "priority": card.priority,
        "status": card.status,
        "source": card.source,
    }


def _dict_to_card(d: dict) -> StrategyCard:
    return StrategyCard(
        id=d["id"],
        title=d["title"],
        scenario_tags=d["scenario_tags"],
        trigger_keywords=d["trigger_keywords"],
        actions=d["actions"],
        priority=d["priority"],
        status=d["status"],
        source=d.get("source"),
    )


def retrieve(
    signals: TaskSignals,
    cards_dir: str = "cards",
    state_file: str = "retrieval_state.json",
    request_id: str = "default",
    *,
    max_cards: int = 2,
    max_tokens: int = 300,
    degrade_threshold: float = 0.3,
    high_confidence_threshold: float = 0.5,
    top_n_for_degrade_fallback: int = 3,
    ttl_seconds: int = 0,
) -> StrategyPacket:
    # 1. state gate
    state = {}
    if os.path.exists(state_file):
        try:
            with open(state_file) as f:
                loaded_state = json.load(f)
            if isinstance(loaded_state, dict):
                state = loaded_state
        except (json.JSONDecodeError, OSError):
            # 状态文件只是防重入缓存；为空/损坏时不应阻断检索链路，按空状态自愈重写。
            state = {}

    # N7: 过期 state 条目清理
    if ttl_seconds > 0:
        now = time.time()
        stale_keys = [
            k for k, v in state.items()
            if isinstance(v, dict) and "created_at" in v and now - v["created_at"] > ttl_seconds
        ]
        for k in stale_keys:
            del state[k]

    if request_id in state:
        entry = state[request_id]
        cards = [_dict_to_card(c) for c in entry["cards"]]
        text = entry["text"]
        tokens = entry["tokens"]
        degraded = entry["degraded"]
        top_scores = entry.get("top_scores", [])
        return StrategyPacket(
            cards=cards,
            text=text,
            tokens=tokens,
            degraded=degraded,
            retrieved=False,
            top_scores=top_scores,
        )

    # 2. load cards
    all_cards = load_active_cards(cards_dir)
    if not all_cards:
        text = compile_packet([])
        tokens = estimate_tokens(text)
        state[request_id] = {
            "cards": [],
            "cards_ids": [],
            "text": text,
            "tokens": tokens,
            "degraded": False,
            "retrieved": True,
            "top_scores": [],
            "created_at": time.time(),
        }
        with open(state_file, "w") as f:
            json.dump(state, f)
        return StrategyPacket(
            cards=[],
            text=text,
            tokens=tokens,
            degraded=False,
            retrieved=True,
            top_scores=[],
        )

    # 3. score & sort
    scored = [(card, score_card(card, signals)) for card in all_cards]
    scored.sort(key=lambda x: x[1], reverse=True)

    # 4. degrade gate
    if scored[0][1] <= degrade_threshold:
        degraded = True
        general_cards = [c for c in all_cards if "general" in c.scenario_tags]
        general_cards.sort(key=lambda c: c.priority, reverse=True)
        selected = general_cards[:top_n_for_degrade_fallback]
        selected_scores = [score_card(c, signals) for c in selected]
    else:
        degraded = False
        relevant_pairs = [pair for pair in scored if pair[1] >= degrade_threshold]
        # 保守注入（GPT-5.5 P0-2）：置信度决定卡数。
        # 高置信(top1>=high_confidence_threshold) → 最多 max_cards 张。
        # 中置信(degrade_threshold<=top1<high_confidence_threshold) → 单卡模式，
        # 只注入 1 张，防多张各自合理但合起来打架（GPT-5.5 指出的多卡叠加冲突）。
        effective_cap = max_cards if relevant_pairs[0][1] >= high_confidence_threshold else 1
        selected_pairs = relevant_pairs[:effective_cap]
        selected = [pair[0] for pair in selected_pairs]
        selected_scores = [pair[1] for pair in selected_pairs]

    # 5. token gate
    final_cards = []
    final_scores = []
    for card, score in zip(selected, selected_scores):
        test_cards = final_cards + [card]
        test_text = compile_packet(test_cards)
        test_tokens = estimate_tokens(test_text)
        if test_tokens > max_tokens:
            break
        final_cards.append(card)
        final_scores.append(score)

    text = compile_packet(final_cards)
    tokens = estimate_tokens(text)

    # 6. write state
    state[request_id] = {
        "cards": [_card_to_dict(c) for c in final_cards],
        "cards_ids": [c.id for c in final_cards],
        "text": text,
        "tokens": tokens,
        "degraded": degraded,
        "retrieved": True,
        "top_scores": final_scores,
        "created_at": time.time(),
    }
    with open(state_file, "w") as f:
        json.dump(state, f)

    return StrategyPacket(
        cards=final_cards,
        text=text,
        tokens=tokens,
        degraded=degraded,
        retrieved=True,
        top_scores=final_scores,
    )
