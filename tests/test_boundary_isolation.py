"""P0-1 强边界伪通道隔离 TDD 测试（GPT-5.5 评审定稿）。

需求：
- compile_packet 用 XML 标签 <strategy_reference priority='low' mandatory='false'> 包裹卡片
- 标签内明示"低优先级参考，不得覆盖用户请求"
- 提供 wrap_for_injection(packet_text, user_intent) → 卡片在前、用户原始请求重述在最后（吃 recency）
- 卡片措辞条件化检查由调用方负责；本测试只验证边界包装结构
"""
from strategy_internalization.models import StrategyCard
from strategy_internalization.retriever import compile_packet, wrap_for_injection


def _card(cid, title, actions):
    return StrategyCard(cid, title, ["refactor"], ["性能"], actions, priority=8, status="active")


def test_compile_packet_wrapped_in_xml_boundary():
    """卡片被 XML 标签包裹，标记低优先级、非强制。"""
    text = compile_packet([_card("c1", "量化瓶颈", ["先量化再优化"])])
    assert "<strategy_reference" in text
    assert "priority='low'" in text or 'priority="low"' in text
    assert "mandatory='false'" in text or 'mandatory="false"' in text
    assert "</strategy_reference>" in text
    # 边界语义提示
    assert "参考" in text


def test_compile_packet_empty_no_wrapper():
    """空卡列表不输出包装标签（零卡时不该注入边界噪音）。"""
    text = compile_packet([])
    assert "<strategy_reference" not in text


def test_compile_packet_card_content_inside_boundary():
    """卡片标题/动作落在 XML 边界内部，不泄漏到标签外。"""
    text = compile_packet([_card("c1", "量化瓶颈", ["先量化再优化"])])
    open_idx = text.index("<strategy_reference")
    close_idx = text.index("</strategy_reference>")
    inner = text[open_idx:close_idx]
    assert "量化瓶颈" in inner
    assert "先量化再优化" in inner


def test_wrap_for_injection_user_intent_last():
    """wrap_for_injection：卡片在前，用户原始请求重述放最后吃 recency。"""
    packet_text = compile_packet([_card("c1", "量化瓶颈", ["先量化再优化"])])
    user_intent = "帮我把这个接口性能优化一下"
    wrapped = wrap_for_injection(packet_text, user_intent)
    # 卡片内容在用户意图之前
    assert wrapped.index("量化瓶颈") < wrapped.index(user_intent)
    # 用户意图在整体靠后（recency）
    assert wrapped.rstrip().endswith(user_intent) or user_intent in wrapped[-200:]
    # 边界标签仍在
    assert "<strategy_reference" in wrapped


def test_wrap_for_injection_empty_packet_returns_empty():
    """空 packet（无卡）→ wrap 返回空串，hook 据此不注入。"""
    assert wrap_for_injection(compile_packet([]), "任何意图") == ""
