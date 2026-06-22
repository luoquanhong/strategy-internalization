"""system_design shadow 卡子类聚类（E, P4）。

按关键词把同场景 shadow 聚成子类，帮后续精细化治理。
每张卡归入第一个匹配的子类（不重复）。
"""
from pathlib import Path
import yaml

# 子类 → 关键词集（GPT-5.5 建议7类 + other 兜底）
SUBTOPIC_KEYWORDS = {
    "boundary":      {"边界", "解耦", "隔离", "契约", "分层", "模块化"},
    "state":         {"状态", "缓存", "外部化", "持久化", "快照"},
    "extensibility": {"扩展", "弹性", "伸缩", "动态", "插件"},
    "failure":       {"熔断", "降级", "回滚", "预案", "容错", "重试", "止损", "红线"},
    "dataflow":      {"反馈", "闭环", "管道", "数据流", "回调", "观测"},
    "complexity":    {"拆分", "简化", "最小化", "剥离", "降噪", "收敛"},
    # other 兜底，不放关键词
}
SUBTOPIC_ORDER = ["boundary", "state", "extensibility", "failure",
                  "dataflow", "complexity", "other"]


def cluster_by_subtopic(shadow_dir, scenario="system_design") -> dict:
    """把指定场景的 shadow 卡按子主题聚类。

    Returns: {subtopic: [card_id, ...]}，含所有子类（other 兜底）。
    """
    result = {sub: [] for sub in SUBTOPIC_ORDER}
    for fp in sorted(Path(shadow_dir).glob("*.yaml")):
        with open(fp) as f:
            data = yaml.safe_load(f) or {}
        tags = data.get("scenario_tags") or []
        if scenario not in tags:
            continue
        kws = data.get("trigger_keywords") or []
        kw_text = "".join(str(k) for k in kws)
        placed = False
        for sub in SUBTOPIC_ORDER:
            if sub == "other":
                continue
            if any(kw in kw_text for kw in SUBTOPIC_KEYWORDS[sub]):
                result[sub].append(data.get("id", fp.stem))
                placed = True
                break
        if not placed:
            result["other"].append(data.get("id", fp.stem))
    return result
