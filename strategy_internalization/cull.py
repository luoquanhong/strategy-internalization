"""shadow 卡淘汰判定器（D, P8）。

五指标（GPT-5.5 定），命中 ≥2 建议淘汰：
1. 长期零命中：hit_count=0 且存在 ≥30 天
2. 与 active 高重叠：≥6 关键词
3. 只提供泛化建议：action 具体性 <0.5
4. 场景触发不清：trigger_keywords <4 个
5. （与现有策略冲突：难自动判定，留人工，本版跳过）

保守原则：单指标不淘汰，≥2 指标才建议淘汰。
"""
from dataclasses import dataclass, field
from pathlib import Path
import os, time, yaml
from strategy_internalization.tier import _specificity

ZERO_HIT_DAYS = 30
OVERLAP_THRESHOLD = 6
SPECIFICITY_THRESHOLD = 0.5
MIN_KEYWORDS = 4
CULL_MIN_INDICATORS = 2


@dataclass
class CullVerdict:
    id: str
    should_cull: bool
    reasons: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


def _load(d):
    cards = []
    for fp in sorted(Path(d).glob("*.yaml")):
        with open(fp) as f:
            data = yaml.safe_load(f) or {}
        cards.append({
            "id": data.get("id", fp.stem),
            "keywords": set(data.get("trigger_keywords") or []),
            "actions": data.get("actions") or [],
            "path": fp,
            "mtime": os.path.getmtime(fp),
        })
    return cards


def evaluate_culling(shadow_dir, active_dir, hits=None, now=None,
                     zero_hit_days=ZERO_HIT_DAYS,
                     overlap_threshold=OVERLAP_THRESHOLD) -> list:
    """评估所有 shadow 卡的淘汰指标，返回 list[CullVerdict]。

    Args:
        hits: {card_id: hit_count}，缺失的卡当作零命中
        now: 当前时间戳（默认 time.time()），用于算卡龄
    """
    hits = hits or {}
    now = now or time.time()
    actives = _load(active_dir)
    shadows = _load(shadow_dir)
    active_kw_sets = [a["keywords"] for a in actives]

    verdicts = []
    for sc in shadows:
        kw = sc["keywords"]
        hit_count = hits.get(sc["id"], 0)
        age_days = (now - sc["mtime"]) / 86400

        m = {}
        reasons = []

        # 指标1：长期零命中
        m["zero_hit_long_standing"] = (hit_count == 0 and age_days >= zero_hit_days)
        if m["zero_hit_long_standing"]:
            reasons.append(f"零命中且已存在 {age_days:.0f} 天（≥{zero_hit_days}）")

        # 指标2：与 active 高重叠
        max_overlap = max((len(kw & ak) for ak in active_kw_sets), default=0)
        m["high_overlap_active"] = max_overlap >= overlap_threshold
        if m["high_overlap_active"]:
            reasons.append(f"与 active 重叠 {max_overlap} 关键词（≥{overlap_threshold}）")

        # 指标3：只提供泛化建议
        spec = _specificity(sc["actions"])
        m["only_generic_advice"] = spec < SPECIFICITY_THRESHOLD
        if m["only_generic_advice"]:
            reasons.append(f"action 具体性 {spec:.2f}（<{SPECIFICITY_THRESHOLD}，泛化建议）")

        # 指标4：场景触发不清
        m["trigger_unclear"] = len(kw) < MIN_KEYWORDS
        if m["trigger_unclear"]:
            reasons.append(f"trigger_keywords 仅 {len(kw)} 个（<{MIN_KEYWORDS}，触发不清）")

        should_cull = len(reasons) >= CULL_MIN_INDICATORS
        verdicts.append(CullVerdict(
            id=sc["id"], should_cull=should_cull,
            reasons=reasons, metrics=m,
        ))
    return verdicts
