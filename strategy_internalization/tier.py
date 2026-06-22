"""shadow 卡轻量分层器（B, P9）。

基于可测量信号（关键词重叠 + action 具体性）把 shadow 卡分三类，无 LLM：
- high_dup（强重复）：与 active 重叠 ≥6 关键词，或与兄弟 shadow 重叠 ≥8
- high_potential（高潜）：与 active 重叠 ≤3 且 action 具体性 ≥0.5
- observe（待观察）：其余
"""
from dataclasses import dataclass
from pathlib import Path
import yaml

DUP_ACTIVE_THRESHOLD = 6
DUP_SHADOW_THRESHOLD = 8
UNIQUE_THRESHOLD = 3
SPECIFICITY_THRESHOLD = 0.5

# 模糊词：出现在 action 里说明该 action 是泛化建议而非可执行步骤
FUZZY_WORDS = ("注意", "考虑", "确保", "尽量", "应该", "可能", "适当", "合理",
               "关注", "重视", "小心", "警惕")


@dataclass
class TieredCard:
    id: str
    reason: str


@dataclass
class TierReport:
    high_dup: list          # list[TieredCard]
    observe: list
    high_potential: list


def _load(d) -> list:
    cards = []
    for fp in sorted(Path(d).glob("*.yaml")):
        with open(fp) as f:
            data = yaml.safe_load(f) or {}
        cards.append({
            "id": data.get("id", fp.stem),
            "keywords": set(data.get("trigger_keywords") or []),
            "actions": data.get("actions") or [],
        })
    return cards


def _is_specific(action: str) -> bool:
    """具体 action：长度 ≥10 且不含模糊词。"""
    return len(action) >= 10 and not any(w in action for w in FUZZY_WORDS)


def _specificity(actions) -> float:
    if not actions:
        return 0.0
    return sum(_is_specific(a) for a in actions) / len(actions)


def tier_shadow_cards(active_dir, shadow_dir,
                      dup_active_threshold=DUP_ACTIVE_THRESHOLD,
                      dup_shadow_threshold=DUP_SHADOW_THRESHOLD,
                      unique_threshold=UNIQUE_THRESHOLD) -> TierReport:
    """分层 shadow 卡。返回三类 + 人话理由。"""
    actives = _load(active_dir)
    shadows = _load(shadow_dir)

    high_dup, observe, high_potential = [], [], []

    for i, sc in enumerate(shadows):
        kw = sc["keywords"]

        active_overlaps = [(a["id"], len(kw & a["keywords"])) for a in actives]
        max_active = max((o for _, o in active_overlaps), default=0)
        max_active_id = (max(active_overlaps, key=lambda x: x[1])[0]
                         if active_overlaps else None)

        sib_overlaps = [(s["id"], len(kw & s["keywords"]))
                        for j, s in enumerate(shadows) if j != i]
        max_shadow = max((o for _, o in sib_overlaps), default=0)
        max_shadow_id = (max(sib_overlaps, key=lambda x: x[1])[0]
                         if sib_overlaps else None)

        spec = _specificity(sc["actions"])

        if max_active >= dup_active_threshold:
            reason = (f"与 active {max_active_id} 重叠 {max_active} 个关键词"
                      f"（≥{dup_active_threshold}）")
            high_dup.append(TieredCard(sc["id"], reason))
        elif max_shadow >= dup_shadow_threshold:
            reason = (f"与 shadow {max_shadow_id} 重叠 {max_shadow} 个关键词"
                      f"（≥{dup_shadow_threshold}）")
            high_dup.append(TieredCard(sc["id"], reason))
        elif max_active <= unique_threshold and spec >= SPECIFICITY_THRESHOLD:
            reason = (f"与 active 最大重叠 {max_active}（≤{unique_threshold}）"
                      f"+ action 具体性 {spec:.2f}")
            high_potential.append(TieredCard(sc["id"], reason))
        else:
            reason = (f"重叠 {max_active}/具体性 {spec:.2f}（中间地带，待观察）")
            observe.append(TieredCard(sc["id"], reason))

    return TierReport(high_dup=high_dup, observe=observe,
                      high_potential=high_potential)
