"""signal_extractor 测试 — 方案A纯规则触发的核心。"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategy_internalization.signal_extractor import (
    extract_signals,
    get_request_id,
    SUPPLEMENTAL_KEYWORDS,
    _get_scenario_keywords,
    rebuild_scenario_keywords,
)


# ── extract_signals：正常场景命中 ──────────────────────────

def test_bug_fix_scenario():
    """调试Bug场景应识别为 bug_fix。"""
    sig = extract_signals("帮我排查这个报错，try except 吞掉了异常")
    assert sig is not None
    assert sig.scenario == "bug_fix"
    assert "报错" in sig.keywords


def test_ops_config_scenario():
    """配置部署场景应识别为 ops_config。"""
    sig = extract_signals("帮我改一下这个 config 配置文件的参数")
    assert sig is not None
    assert sig.scenario == "ops_config"
    assert "配置" in sig.keywords


def test_refactor_scenario():
    """性能优化场景应识别为 refactor。"""
    sig = extract_signals("这个接口太慢了，帮我优化性能，定位瓶颈")
    assert sig is not None
    assert sig.scenario == "refactor"


def test_new_build_scenario():
    """新建项目场景应识别为 new_build。"""
    sig = extract_signals("帮我从零搭建一个新项目，初始化好")
    assert sig is not None
    assert sig.scenario == "new_build"


# ── extract_signals：多场景竞争 ──────────────────────────

def test_multi_scenario_picks_most_hits():
    """多个场景都命中时，选命中词最多的那个。"""
    # bug_fix 命中2个（报错、异常），ops_config 命中1个（参数）
    sig = extract_signals("报错了异常了，参数也有问题")
    assert sig is not None
    assert sig.scenario == "bug_fix"


# ── extract_signals：不触发（返回 None）──────────────────

def test_pure_chitchat_returns_none():
    """纯闲聊不触发检索。"""
    assert extract_signals("你好呀，今天天气不错") is None
    assert extract_signals("早上好") is None
    assert extract_signals("谢谢亲爱的") is None


def test_single_accidental_hit_returns_none():
    """单个弱信号偶然命中词不触发（低置信保护）。"""
    # 只命中1个词"测试"，但"测试"在 general 卡的 trigger_keywords 里，
    # 不在派生关键词表里 → 不触发
    assert extract_signals("你好测试一下") is None
    # 只命中1个弱信号词"慢"（"你回复好慢啊"可能是闲聊吐槽），不强信号 → 不触发
    assert extract_signals("你回复好慢啊") is None
    # 只命中1个弱信号词"版本"，不是强信号 → 不触发
    assert extract_signals("你用的什么版本") is None


def test_daily_context_strong_words_do_not_trigger():
    """N1: 强信号词出现在日常语境时不能误触发策略检索。"""
    for text in [
        "配置生活，优化心情",
        "我今天模型玩得很开心",
        "这个电影 bug 太多了",
        "我准备从零开始写日记",
    ]:
        assert extract_signals(text) is None, f"闲聊不应触发: {text}"


def test_real_technical_single_strong_signal_still_triggers():
    """保护测试：语境消歧不能误杀真实技术单强信号任务。"""
    cases = [
        ("帮我测一下 glm-5.2 这个模型能不能用", "ops_config"),
        ("帮我把 config 配置文件改一下", "ops_config"),
        ("这个接口报错了，帮我看看", "bug_fix"),
        ("连接超时怎么排查", "bug_fix"),
        ("帮我 debug 一下", "bug_fix"),
    ]
    for text, expected in cases:
        sig = extract_signals(text)
        assert sig is not None, f"真实技术任务不应被误杀: {text}"
        assert sig.scenario == expected


def test_strong_signal_single_keyword_triggers():
    """强信号词单独出现（只有1个命中词）也触发检索。

    回归保护：2026-06-15 修复漏触发 bug。
    原来需要凑够 2 个关键词才触发，导致"这个接口报错了"只命中"报错"1个词被拦截。
    """
    # "报错"单独出现 → 应触发（最典型的 bug 修复口头禅）
    sig = extract_signals("这个接口报错了，帮我看看")
    assert sig is not None
    assert sig.scenario == "bug_fix"
    # "模型"单独出现 → 应触发
    sig = extract_signals("帮我测一下glm-5.2这个模型能不能用")
    assert sig is not None
    assert sig.scenario == "ops_config"
    # "配置"单独出现 → 应触发
    sig = extract_signals("帮我把配置改一下")
    assert sig is not None
    assert sig.scenario == "ops_config"
    # "性能"单独出现 → 应触发
    sig = extract_signals("帮我看看性能问题")
    assert sig is not None
    assert sig.scenario == "refactor"


def test_empty_text_returns_none():
    """空文本不触发。"""
    assert extract_signals("") is None
    assert extract_signals("   ") is None
    assert extract_signals(None) is None  # type: ignore


# ── extract_signals：边界 ────────────────────────────────

def test_case_insensitive_english():
    """英文关键词大小写不敏感。"""
    sig = extract_signals("Fix this BUG, check the Model config")
    assert sig is not None
    assert "bug" in sig.keywords


def test_keywords_deduplicated():
    """同一词多次出现只算一次。"""
    sig = extract_signals("报错报错报错异常异常")
    assert sig is not None
    # 去重后只有 报错、异常 两个
    assert len(sig.keywords) == 2


def test_text_preserved_in_signal():
    """原文保留在 signal.text 里。"""
    text = "帮我修复这个bug"
    sig = extract_signals(text)
    assert sig is not None
    assert sig.text == text


# ── get_request_id ───────────────────────────────────────

def test_request_id_same_content_same_window():
    """同一内容同一时间窗 → 同一 ID。"""
    rid1 = get_request_id("修复bug")
    rid2 = get_request_id("修复bug")
    assert rid1 == rid2


def test_request_id_different_content():
    """不同内容 → 不同 ID。"""
    rid1 = get_request_id("修复bug")
    rid2 = get_request_id("优化性能")
    assert rid1 != rid2


def test_request_id_format():
    """ID 格式：{hour_window}_{8位hash}。"""
    rid = get_request_id("test")
    parts = rid.split("_")
    assert len(parts) == 2
    assert parts[0].isdigit()  # 时间窗是数字
    assert len(parts[1]) == 8  # hash 8位


# ── 数据完整性 ───────────────────────────────────────────

def test_all_scenario_tags_cover_active_cards():
    """信号表覆盖所有 active 卡的 scenario_tags（除 general）。"""
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    rebuild_scenario_keywords()
    kw = _get_scenario_keywords(str(repo / "cards"))
    expected = {"bug_fix", "new_build", "ops_config", "refactor"}
    assert expected.issubset(set(kw.keys()))


def test_general_not_in_keyword_table():
    """general 是降级兜底，不该有主动触发词。"""
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    rebuild_scenario_keywords()
    kw = _get_scenario_keywords(str(repo / "cards"))
    assert "general" not in kw


# ── 回归测试：N2/N6/坑B-C 场景覆盖与死词 ─────────────────────

def test_high_frequency_technical_tasks_are_not_transparent():
    """N2: review/安全/网络/测试/文档/系统设计等高频技术任务不能漏触发。"""
    cases = [
        ("帮我 review 一下这段代码", "review"),
        ("做个安全审查", "review"),
        ("Cookie 泄露了怎么处理", "cost_safety"),
        ("DNS 解析失败", "bug_fix"),
        ("连接超时怎么排查", "bug_fix"),
        ("帮我写单元测试", "test"),
        ("测试覆盖率太低", "test"),
        ("设计一个微服务架构", "system_design"),
        ("帮我做个技术方案评审", "review"),
        ("审计一下这个模块的安全性", "review"),
        ("帮我写个 API 文档", "system_design"),
    ]
    for text, expected_scenario in cases:
        sig = extract_signals(text)
        assert sig is not None, f"应触发但返回 None: {text}"
        assert sig.scenario == expected_scenario, f"{text} 应归类为 {expected_scenario}，实际 {sig.scenario}"


def test_active_card_non_general_trigger_keywords_are_extractable():
    """坑B/C: active 非 general 卡片声明的 trigger_keywords 自动进入派生字典（根治后不再有死词）。"""
    from pathlib import Path
    from strategy_internalization.retriever import load_active_cards
    repo = Path(__file__).resolve().parents[1]
    rebuild_scenario_keywords()
    kw = _get_scenario_keywords(str(repo / "cards"))
    dict_words = set()
    for words in kw.values():
        dict_words.update(words)
    for card in load_active_cards(str(repo / "cards")):
        if "general" in card.scenario_tags:
            continue
        for trigger_kw in card.trigger_keywords:
            assert trigger_kw in dict_words, f"active 卡 trigger_keywords {trigger_kw!r} 必须在派生字典里"


def test_shadow_scenarios_are_represented_in_keyword_table():
    """N6: shadow 主体场景应在补充词有入口，避免晋升后仍不可检索。"""
    for scenario in ["system_design", "cost_safety", "review", "test"]:
        assert scenario in SUPPLEMENTAL_KEYWORDS, f"shadow 高频场景 {scenario} 必须有补充词入口"
        assert SUPPLEMENTAL_KEYWORDS[scenario], f"场景 {scenario} 不能是空词表"


# ── 漂移根治测试（2026-06-17）：卡片为单一数据源 ─────────

import yaml as _yaml

def _write_signal_card(cards_dir, card_id, *, scenario_tags, trigger_keywords, priority=5, status="active"):
    """测试用：写一张卡到指定目录。"""
    os.makedirs(cards_dir, exist_ok=True)
    data = {
        "id": card_id,
        "title": f"Test {card_id}",
        "scenario_tags": scenario_tags,
        "trigger_keywords": trigger_keywords,
        "actions": ["act"],
        "priority": priority,
        "status": status,
        "source": None,
    }
    with open(os.path.join(cards_dir, f"{card_id}.yaml"), "w") as f:
        _yaml.dump(data, f, default_flow_style=False)


def test_drift_fixed_new_card_keyword_auto_recognized(tmp_path):
    """根治：新增 active 卡的 trigger_keyword 自动被提取器识别，零代码改动。"""
    from strategy_internalization.signal_extractor import extract_signals, rebuild_scenario_keywords
    cards_dir = str(tmp_path / "cards")
    _write_signal_card(cards_dir, "c1", scenario_tags=["bug_fix"],
                       trigger_keywords=["幻影词zzz", "幽灵词yyy"], priority=5)
    rebuild_scenario_keywords()
    # 用两个卡片派生词确保越过阈值（非强信号词需≥2个）
    sig = extract_signals("出现幻影词zzz和幽灵词yyy了", cards_dir=cards_dir)
    assert sig is not None, "卡片新增的 trigger_keyword 必须自动被识别"
    assert "幻影词zzz" in sig.keywords
    assert "幽灵词yyy" in sig.keywords
    assert sig.scenario == "bug_fix"


def test_drift_fixed_removed_card_keyword_gone(tmp_path):
    """根治：从卡片删掉 trigger_keyword 后，提取器不再识别该词（除非在补充层）。"""
    from strategy_internalization.signal_extractor import extract_signals, rebuild_scenario_keywords, SUPPLEMENTAL_KEYWORDS
    cards_dir = str(tmp_path / "cards")
    # 只有一张卡，trigger_keywords 不含测试目标词
    _write_signal_card(cards_dir, "c1", scenario_tags=["bug_fix"],
                       trigger_keywords=["崩溃"], priority=5)
    rebuild_scenario_keywords()
    # 用一个确定不在补充层的造词
    target = "幻影删词qqq"
    all_supplemental = set()
    for words in SUPPLEMENTAL_KEYWORDS.values():
        all_supplemental.update(words)
    assert target not in all_supplemental, "测试前提：造词不在补充层"
    # 造词不在任何卡片里，也不在补充层 → 不应被识别
    sig = extract_signals(f"帮我处理{target}问题", cards_dir=cards_dir)
    if sig is not None:
        assert target not in sig.keywords, "删掉的卡片关键词不应再被识别"


def test_drift_fixed_new_scenario_auto_discovered(tmp_path):
    """根治：新增场景的 active 卡自动被提取器发现，无需手动加场景 key。"""
    from strategy_internalization.signal_extractor import extract_signals, rebuild_scenario_keywords
    cards_dir = str(tmp_path / "cards")
    _write_signal_card(cards_dir, "c1", scenario_tags=["data_pipeline"],
                       trigger_keywords=["etl", "数据管道"], priority=5)
    rebuild_scenario_keywords()
    # 用两个关键词确保越过阈值
    sig = extract_signals("帮我搭个etl数据管道", cards_dir=cards_dir)
    assert sig is not None, "新场景应自动被发现"
    assert sig.scenario == "data_pipeline"


def test_supplemental_keywords_still_trigger_without_card(tmp_path):
    """补充层词（卡片没有的口语/同义词）仍能触发，不依赖卡片声明。"""
    from strategy_internalization.signal_extractor import extract_signals, rebuild_scenario_keywords, SUPPLEMENTAL_KEYWORDS
    cards_dir = str(tmp_path / "cards")
    # 只有一张卡，trigger_keywords 不含 "bug"
    _write_signal_card(cards_dir, "c1", scenario_tags=["bug_fix"],
                       trigger_keywords=["崩溃"], priority=5)
    rebuild_scenario_keywords()
    # 确认 "bug" 在补充层
    assert "bug" in SUPPLEMENTAL_KEYWORDS.get("bug_fix", []), "测试前提：bug 在补充层"
    sig = extract_signals("this is a bug", cards_dir=cards_dir)
    assert sig is not None, "补充层词应触发"
    assert "bug" in sig.keywords


def test_general_card_keywords_not_in_derived_dict(tmp_path):
    """general 卡的 trigger_keywords 不进入派生字典（仍是降级兜底）。"""
    from strategy_internalization.signal_extractor import extract_signals, rebuild_scenario_keywords
    cards_dir = str(tmp_path / "cards")
    _write_signal_card(cards_dir, "gen", scenario_tags=["general"],
                       trigger_keywords=["验证", "闭环", "测试"], priority=5)
    rebuild_scenario_keywords()
    # "验证" 只在 general 卡里 → 不应触发
    sig = extract_signals("帮我验证一下", cards_dir=cards_dir)
    assert sig is None, "general 卡的关键词不应主动触发"


def test_real_cards_no_dead_keywords_after_refactor():
    """实仓回归：重构后 active 非 general 卡的 trigger_keywords 全部可提取。"""
    from strategy_internalization.signal_extractor import extract_signals, rebuild_scenario_keywords
    from strategy_internalization.retriever import load_active_cards
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    rebuild_scenario_keywords()
    # 用一张真实 active 卡的 trigger_keyword 造一句话，确认能触发
    cards = load_active_cards(str(repo / "cards"))
    non_general = [c for c in cards if "general" not in c.scenario_tags]
    assert non_general, "实仓必须有非 general active 卡"
    # 取第一张卡的第一个 trigger_keyword
    kw = non_general[0].trigger_keywords[0]
    sig = extract_signals(f"帮我处理{kw}问题", cards_dir=str(repo / "cards"))
    assert sig is not None, f"实仓关键词 {kw!r} 必须能触发"
    assert kw in sig.keywords
