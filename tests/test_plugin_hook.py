"""strategy-injection 插件 hook 集成测试。

直接 import 插件模块的 _pre_llm_call，验证回调契约：
- 闲聊 → {}（不注入）
- 技术任务 → {"context": ...} 含 XML 边界 + 用户请求重述
- 缺 user_message → {}
- 异常 → fail-open 返回 {}
- 回调能吸收 Hermes 透传的额外 kwargs（telemetry_schema_version 等）
"""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

# 相对定位插件 __init__.py（不依赖绝对路径）
_HERE = Path(__file__).resolve().parent
PLUGIN_INIT = _HERE.parent / "plugin" / "__init__.py"
# 引擎需在 sys.path 上，供插件 import strategy_internalization
_ENGINE = _HERE.parent
if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))


@pytest.fixture(scope="module")
def plugin_mod():
    spec = importlib.util.spec_from_file_location("strategy_injection_plugin", PLUGIN_INIT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_chitchat_returns_empty(plugin_mod):
    assert plugin_mod._pre_llm_call(user_message="今天天气真好啊") == {}


def test_missing_message_returns_empty(plugin_mod):
    assert plugin_mod._pre_llm_call(user_message=None) == {}
    assert plugin_mod._pre_llm_call(user_message="") == {}
    assert plugin_mod._pre_llm_call() == {}


def test_technical_task_injects_context(plugin_mod):
    msg = "帮我优化一下这个接口的性能瓶颈"
    out = plugin_mod._pre_llm_call(user_message=msg)
    assert "context" in out
    ctx = out["context"]
    # 强边界 XML
    assert "<strategy_reference" in ctx
    assert "</strategy_reference>" in ctx
    # 用户请求重述吃 recency（在末尾）
    assert msg in ctx[-200:]


def test_absorbs_extra_kwargs(plugin_mod):
    """Hermes 会透传一堆 kwargs，回调必须不报错。"""
    out = plugin_mod._pre_llm_call(
        user_message="这个yaml配置改完模型探活报错了",
        session_id="s1", task_id="t1", turn_id=1, model="x",
        platform="feishu", sender_id="u1", is_first_turn=True,
        conversation_history=[], telemetry_schema_version=99,
    )
    assert "context" in out


def test_fail_open_on_engine_error(plugin_mod, monkeypatch):
    """引擎抛异常时 fail-open 返回 {}，不向上抛。"""
    import strategy_internalization.signal_extractor as se

    def boom(*a, **k):
        raise RuntimeError("engine down")

    monkeypatch.setattr(se, "extract_signals", boom)
    # 注意：插件内是 from ... import extract_signals，需打补丁到插件已绑定的引用
    # 改为 monkeypatch 模块函数后，插件内每次调用都重新 from import → 生效
    out = plugin_mod._pre_llm_call(user_message="帮我优化性能瓶颈")
    assert out == {}
