"""信号提取器（纯规则，零 LLM）。

把用户任务文本 → TaskSignals（scenario + keywords）。
纯闲聊返回 None，不触发检索。

这是方案A（纯规则触发）的核心：由确定性代码决定"要不要检索"，
不依赖 LLM 判断、不依赖 Hermes Skill 自动匹配。

数据源根治（2026-06-17）：场景关键词表从 active cards 自动派生，
卡片是单一数据源。SUPPLEMENTAL_KEYWORDS 是唯一需要人工维护的扩展层
（卡片没有的同义词/口语词）。改卡片关键词 → 提取器自动同步，不再漂移。
"""

import hashlib
import time
from collections import defaultdict
from typing import Optional

from .models import TaskSignals
from .retriever import load_active_cards


# ── 补充触发词（人工维护的唯一扩展层）────────────────────
# 卡片未声明但应触发的口语/同义词。
# 改卡片关键词会自动同步，这里只放卡片里没有的词。
# 每次新增/修改 active 卡后，检查这里的词是否还需要保留。
SUPPLEMENTAL_KEYWORDS: dict[str, list[str]] = {
    "bug_fix": [
        "bug", "崩溃", "排查", "修复", "调试", "debug", "stack", "traceback",
        "失败", "挂了", "不work", "dns", "DNS", "解析失败", "连接超时", "超时",
    ],
    "new_build": [
        "新建", "从零", "新功能", "新项目", "搭建", "初始化", "创建项目",
        "从头", "搭一个", "建一个", "写一个",
    ],
    "ops_config": [
        "部署", "上线", "yml", "环境变量",
    ],
    "refactor": [
        "重构", "refactor", "卡顿", "提速", "定位瓶颈",
    ],
    "review": [
        "review", "代码审查", "审查", "安全审查", "评审", "技术方案评审",
        "审计", "安全性", "交叉审查", "红蓝对抗", "多模型", "魔鬼代言人",
    ],
    "test": [
        "单元测试", "测试覆盖率", "覆盖率", "端到端", "e2e", "E2E",
        "A/B反向", "基线测试", "验收", "验证流程", "fixture",
    ],
    "system_design": [
        "系统设计", "架构", "微服务", "架构设计", "技术方案", "API 文档", "api 文档",
        "文档", "分层", "模块化", "路由", "设计",
    ],
    "cost_safety": [
        "成本", "预算", "烧钱", "按量计费", "熔断", "止损", "红线",
        "cookie", "Cookie", "泄露", "凭证", "密钥", "安全", "高风险", "权限",
    ],
}

# 触发阈值：命中的独立关键词数达到此值才触发检索（防止"你好啊测试一下"误触发）。
TRIGGER_MIN_KEYWORDS = 2

# 强信号词：这些词单独出现即足以判定是技术任务，不受 TRIGGER_MIN_KEYWORDS 约束。
# 选取标准：在日常闲聊中几乎不会出现，或出现时注入策略卡也无害。
STRONG_SIGNALS: set[str] = {
    # bug_fix — 闲聊几乎不可能用这些词
    "报错", "崩溃", "traceback", "stack", "bug", "debug", "异常", "绕过", "吞掉",
    # ops_config — 技术专有名词或强技术动词
    "配置", "config", "yaml", "yml", "模型", "model", "参数", "探活", "部署", "调参",
    # refactor — 技术场景专属
    "重构", "refactor", "性能", "优化", "瓶颈",
    # new_build — 技术场景专属
    "初始化", "从零", "搭建",
    # review/test/system_design/cost_safety — 高频技术任务锚点
    "review", "审查", "评审", "审计", "单元测试", "测试覆盖率", "架构", "微服务",
    "API 文档", "api 文档", "Cookie", "cookie", "泄露", "DNS", "dns", "连接超时",
}

# 明确日常语境：这些搭配命中技术词但不是技术任务，应在强信号豁免前拦截。
DAILY_CONTEXT_PATTERNS: tuple[str, ...] = (
    "配置生活",
    "优化心情",
    "模型玩得很开心",
    "电影 bug",
    "从零开始写日记",
)

# 技术语境锚点：即使有日常词，也出现这些词时按技术任务处理。
TECH_CONTEXT_ANCHORS: tuple[str, ...] = (
    "接口", "代码", "文件", "系统", "服务", "服务器", "模块", "项目", "仓库",
    "api", "API", "函数", "测试", "单元测试", "覆盖率", "报错", "异常", "traceback",
    "debug", "调试", "排查", "配置文件", "config", "yaml", "yml", "glm", "模型能不能用",
    "连接超时", "DNS", "dns", "审查", "审计", "评审", "架构", "微服务",
)

# ── 缓存：cards_dir → 派生的场景关键词表 ──────────────────
_keywords_cache: dict[str, dict[str, list[str]]] = {}


def _get_scenario_keywords(cards_dir: str = "cards") -> dict[str, list[str]]:
    """从 active cards 自动派生场景关键词表（单一数据源）+ 合并补充词。

    卡片的 trigger_keywords 是主数据源；SUPPLEMENTAL_KEYWORDS 是人工扩展层。
    general 卡的 trigger_keywords 不进入派生字典（它是降级兜底卡）。
    """
    if cards_dir not in _keywords_cache:
        derived: dict[str, set[str]] = defaultdict(set)
        try:
            cards = load_active_cards(cards_dir)
            for card in cards:
                if "general" in card.scenario_tags:
                    continue
                # 只用第一个非 general 的 scenario_tag 作为关键词的主场景。
                # 其他 tag 仍用于 score_card 的场景匹配，但不用于关键词提取——
                # 否则多 tag 卡的关键词会灌进所有场景，导致场景归属歧义。
                primary_tag = None
                for tag in card.scenario_tags:
                    if tag != "general":
                        primary_tag = tag
                        break
                if primary_tag is None:
                    continue
                for kw in card.trigger_keywords:
                    derived[primary_tag].add(kw)
        except (OSError, ValueError):
            # 卡片目录不存在或损坏时，仍用补充词兜底
            pass
        # 合并补充词
        for scenario, words in SUPPLEMENTAL_KEYWORDS.items():
            for w in words:
                derived[scenario].add(w)
        _keywords_cache[cards_dir] = {k: sorted(v) for k, v in derived.items()}
    return _keywords_cache[cards_dir]


def rebuild_scenario_keywords() -> None:
    """清空缓存，下次调用时从 cards 重新派生。测试用。"""
    _keywords_cache.clear()


def _looks_like_daily_context(text: str) -> bool:
    """判断是否为明确日常语境，避免强信号词误触发。"""
    return any(pattern in text for pattern in DAILY_CONTEXT_PATTERNS) and not any(
        anchor in text for anchor in TECH_CONTEXT_ANCHORS
    )


def extract_signals(
    text: str, cards_dir: str = "cards"
) -> Optional[TaskSignals]:
    """从用户文本提取任务信号。

    返回:
        TaskSignals — 命中任务场景，应触发检索
        None        — 纯闲聊，不触发检索（零额外 token）
    """
    if not text or not text.strip():
        return None

    # 0. 明确日常语境拦截：避免"配置生活/电影bug"等强信号词误触发
    if _looks_like_daily_context(text):
        return None

    text_lower = text.lower()
    scenario_keywords = _get_scenario_keywords(cards_dir)

    # 1. 统计每个 scenario 的命中词
    scenario_hit_counts: dict[str, int] = {}
    matched_keywords: list[str] = []

    for scenario, kws in scenario_keywords.items():
        for kw in kws:
            if kw in text_lower:
                scenario_hit_counts[scenario] = scenario_hit_counts.get(scenario, 0) + 1
                matched_keywords.append(kw)

    # 去重
    matched_keywords = list(dict.fromkeys(matched_keywords))

    # 2. 触发判断：没有任何场景命中词 → 纯闲聊，不触发
    if not matched_keywords:
        return None

    # 3. 决定 scenario：命中词最多的场景胜出
    scenario = max(scenario_hit_counts, key=scenario_hit_counts.get)

    # 4. 低置信保护：只有1个命中词且该场景也只命中1个 → 可能是偶然命中，不触发
    #    但强信号词豁免——"报错""配置""模型"等单独出现就足以判定是技术任务
    has_strong = any(kw in STRONG_SIGNALS for kw in matched_keywords)
    if not has_strong and len(matched_keywords) < TRIGGER_MIN_KEYWORDS and scenario_hit_counts[scenario] < TRIGGER_MIN_KEYWORDS:
        return None

    return TaskSignals(scenario=scenario, keywords=matched_keywords, text=text)


def get_request_id(user_text: str, window_hours: int = 1) -> str:
    """生成检索请求 ID（用于 retrieve 的状态外置防重入）。

    基于消息内容 + 小时级时间窗：
    - 同一时间窗内同一消息内容 → 同一 ID → retrieve 返回缓存（防重入）
    - 不同时间窗或不同消息 → 新检索
    - 状态文件增长可控（每小时每消息最多一条）
    """
    hour_window = int(time.time()) // (3600 * window_hours)
    content_hash = hashlib.md5(user_text.encode("utf-8")).hexdigest()[:8]
    return f"{hour_window}_{content_hash}"
