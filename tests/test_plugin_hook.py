"""strategy-injection 插件 hook 集成测试。

直接 import 插件模块的 _pre_llm_call，验证回调契约：
- 闲聊 → {}（不注入）
- 技术任务 → {"context": ...} 含 XML 边界 + 用户请求重述
- 缺 user_message → {}
- 异常 → fail-open 返回 {}
- 回调能吸收 Hermes 透传的额外 kwargs（telemetry_schema_version 等）
"""
import importlib.util
import sys
from pathlib import Path

import pytest

# 公开版扁平结构：plugin/__init__.py 在仓库根的 plugin/ 目录
PLUGIN_INIT = Path(__file__).resolve().parent.parent / "plugin" / "__init__.py"


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


# ---- P1 实验框架集成测试 ----

def test_p1_experiment_db_records_exposure(plugin_mod, monkeypatch, tmp_path):
    """P1: 技术任务注入后 experiment.db 应记录曝光。"""
    import sqlite3

    db_path = str(tmp_path / "experiment.db")
    state_path = str(tmp_path / "state.json")
    monkeypatch.setattr(plugin_mod, "_DB", db_path)
    monkeypatch.setattr(plugin_mod, "_STATE", state_path)

    msg = "帮我优化一下这个接口的性能瓶颈"
    out = plugin_mod._pre_llm_call(user_message=msg)
    assert "context" in out  # 确认注入了

    # experiment.db 应有曝光记录
    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM exposure").fetchone()[0]
    conn.close()
    assert count >= 1


def test_p1_stale_exposure_marked_as_retry(plugin_mod, monkeypatch, tmp_path):
    """P1: 超时无 outcome 的曝光在下次调用时被标记为 retry。"""
    import sqlite3
    import time

    db_path = str(tmp_path / "experiment.db")
    state_path = str(tmp_path / "state.json")
    monkeypatch.setattr(plugin_mod, "_DB", db_path)
    monkeypatch.setattr(plugin_mod, "_STATE", state_path)

    # 第一次调用：记录曝光
    out1 = plugin_mod._pre_llm_call(user_message="帮我优化一下这个接口的性能瓶颈")
    assert "context" in out1

    # 手动把曝光时间改为 600s 前（超时）
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE exposure SET timestamp = ?", (time.time() - 600,))
    conn.commit()
    conn.close()

    # 第二次调用：应标记旧曝光为 retry
    out2 = plugin_mod._pre_llm_call(user_message="帮我排查这个数据库连接池的泄漏问题")
    # out2 可能为 {} （如果没匹配到卡）或有 context，都行——重点是 retry 被标记

    conn = sqlite3.connect(db_path)
    retry_count = conn.execute(
        "SELECT COUNT(*) FROM outcome WHERE outcome = 'retry'"
    ).fetchone()[0]
    conn.close()
    assert retry_count >= 1


def test_p1_chitchat_no_experiment_side_effect(plugin_mod, monkeypatch, tmp_path):
    """P1: 闲聊不产生曝光记录。"""
    import os

    db_path = str(tmp_path / "experiment.db")
    state_path = str(tmp_path / "state.json")
    monkeypatch.setattr(plugin_mod, "_DB", db_path)
    monkeypatch.setattr(plugin_mod, "_STATE", state_path)

    out = plugin_mod._pre_llm_call(user_message="今天天气真好啊")
    assert out == {}

    # 闲聊不应产生曝光记录（但 DB 会被 init_db 创建——这是正常的）
    if os.path.exists(db_path):
        import sqlite3
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM exposure").fetchone()[0]
        conn.close()
        assert count == 0
