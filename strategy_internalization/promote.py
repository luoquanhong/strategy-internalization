"""shadow 卡晋升评估器（C, P7）。

6 指标（GPT-5.5 定），5 个可自动判，1 个留人工：
1. 有命中次数：hit_count > 0
2. 覆盖 active 盲点：与所有 active 最大重叠 ≤3
3. 与 active 不冲突：与所有 active 最大重叠 <6
4. 表达简洁：3 ≤ len(actions) ≤ 5
5. 可执行性：action 具体性 ≥0.5
6. 失败修复能力：留人工

晋升候选门槛：5 自动指标 ≥4 满足 → promote_ready=True。
"""
from dataclasses import dataclass, field
from pathlib import Path
import yaml
from strategy_internalization.tier import _specificity

BLIND_SPOT_THRESHOLD = 3      # 重叠 ≤3 算覆盖盲点
NO_CONFLICT_THRESHOLD = 6     # 重叠 <6 算不冲突
SPECIFICITY_THRESHOLD = 0.5
PROMOTE_MIN_INDICATORS = 4


@dataclass
class PromoteVerdict:
    id: str
    promote_ready: bool
    metrics: dict = field(default_factory=dict)
    manual_indicators: list = field(default_factory=list)
    reasons: list = field(default_factory=list)


def _load(d):
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


def evaluate_promotion(shadow_dir, active_dir, hits=None,
                       blind_spot_threshold=BLIND_SPOT_THRESHOLD,
                       no_conflict_threshold=NO_CONFLICT_THRESHOLD) -> list:
    """评估所有 shadow 卡的晋升候选度，返回 list[PromoteVerdict]。"""
    hits = hits or {}
    actives = _load(active_dir)
    shadows = _load(shadow_dir)
    active_kw = [a["keywords"] for a in actives]

    verdicts = []
    for sc in shadows:
        kw = sc["keywords"]
        hit_count = hits.get(sc["id"], 0)
        max_overlap = max((len(kw & ak) for ak in active_kw), default=0)
        spec = _specificity(sc["actions"])
        n_actions = len(sc["actions"])

        m = {}
        reasons = []

        m["has_hits"] = hit_count > 0
        if m["has_hits"]:
            reasons.append(f"有命中 {hit_count} 次")

        m["blind_spot_coverage"] = max_overlap <= blind_spot_threshold
        if m["blind_spot_coverage"]:
            reasons.append(f"与 active 最大重叠 {max_overlap}（≤{blind_spot_threshold}，补盲点）")

        m["no_conflict"] = max_overlap < no_conflict_threshold
        if not m["no_conflict"]:
            reasons.append(f"与 active 重叠 {max_overlap}（≥{no_conflict_threshold}，疑似重复）")

        m["concise"] = 3 <= n_actions <= 5
        if not m["concise"]:
            reasons.append(f"actions {n_actions} 条（期望3-5）")

        m["executable"] = spec >= SPECIFICITY_THRESHOLD
        if not m["executable"]:
            reasons.append(f"action 具体性 {spec:.2f}（<{SPECIFICITY_THRESHOLD}）")

        auto_pass = sum(m.values())
        promote_ready = auto_pass >= PROMOTE_MIN_INDICATORS

        verdicts.append(PromoteVerdict(
            id=sc["id"],
            promote_ready=promote_ready,
            metrics=m,
            manual_indicators=["failure_fix"],
            reasons=reasons,
        ))
    return verdicts
