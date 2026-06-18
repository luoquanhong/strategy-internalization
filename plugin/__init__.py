"""策略注入插件 — pre_llm_call hook（P0 定稿 + P1 实验框架集成）。

把策略内化层焊进 LLM 调用前的代码层入口，Agent 无法跳过。
- 闲聊：extract_signals 返回 None → 不注入，零开销
- 命中：retrieve（保守注入 max_cards=2/max_tokens=300 + 中置信单卡）
        → wrap_for_injection（XML 强边界 + 用户原始请求重述吃 recency）
- P1 实验：experiment_db 传入 retrieve → holdout 分流 + penalty 降权 + 曝光记录
           mark_stale_exposures_as_retry → 超时无 outcome 的曝光推定为 retry
- fail-open：任何异常只记日志不中断对话（外层 conversation_loop 已兜，内层再保一道）

零改源码：插件放 ~/.hermes/plugins/，config.yaml 的 plugins.enabled 启用。
"""
import logging
import os
import sys

logger = logging.getLogger("strategy-injection")

# 策略内化层引擎路径（控制平面，纯 Python 零 LLM）
# 引擎目录 = 含 strategy_internalization 包 + cards 的目录。
# 发现优先级：环境变量 > 候选目录探测
_HERE = os.path.dirname(os.path.abspath(__file__))


def _find_engine():
    env = os.environ.get("STRATEGY_ENGINE_PATH")
    if env and os.path.isdir(os.path.join(env, "strategy_internalization")):
        return env
    # 候选：插件同级 / 上级 / 约定子目录
    candidates = [
        _HERE,
        os.path.dirname(_HERE),
        os.path.join(_HERE, "strategy-internalization"),
        os.path.join(os.path.dirname(_HERE), "strategy-internalization"),
    ]
    for c in candidates:
        if (os.path.isdir(os.path.join(c, "strategy_internalization"))
                and os.path.isdir(os.path.join(c, "cards"))):
            return c
    return os.path.dirname(_HERE)  # 兜底


_ENGINE_PATH = _find_engine()
_CARDS = os.path.join(_ENGINE_PATH, "cards")
_STATE = os.path.join(_ENGINE_PATH, "retrieval_state.json")
_DB = os.path.join(_ENGINE_PATH, "experiment.db")  # P1 实验数据库

if _ENGINE_PATH not in sys.path:
    sys.path.insert(0, _ENGINE_PATH)


def _pre_llm_call(*, user_message=None, **kwargs):
    """每轮 LLM 调用前触发。命中技术任务才注入策略卡，否则返回 {}。

    必须接受 **kwargs：Hermes 透传 session_id/task_id/turn_id/model/
    telemetry_schema_version 等一堆参数，不吸收会 TypeError。
    """
    try:
        if not user_message or not str(user_message).strip():
            return {}

        # 延迟 import：放函数内，避免插件加载期引擎缺失就炸整个插件系统
        from strategy_internalization.signal_extractor import (
            extract_signals,
            get_request_id,
        )
        from strategy_internalization.retriever import retrieve, wrap_for_injection
        from strategy_internalization import experiment

        # P1: 初始化实验 DB + 标记超时曝光为 retry
        experiment.init_db(_DB)
        stale = experiment.mark_stale_exposures_as_retry(_DB)
        if stale:
            logger.info("strategy-injection: marked %d stale exposure(s) as retry", stale)

        msg = str(user_message)
        sig = extract_signals(msg, cards_dir=_CARDS)
        if sig is None:
            return {}  # 闲聊，零开销

        packet = retrieve(
            sig,
            cards_dir=_CARDS,
            state_file=_STATE,
            request_id=get_request_id(msg),
            experiment_db=_DB,  # P1: 启用 holdout/penalty/曝光记录
        )
        if not packet.cards:
            return {}

        injected = wrap_for_injection(packet.text, msg)
        if not injected:
            return {}

        logger.info(
            "strategy-injection: scenario=%s cards=%d tokens=%d degraded=%s",
            sig.scenario, len(packet.cards), packet.tokens, packet.degraded,
        )
        return {"context": injected}
    except Exception as exc:
        # fail-open：绝不因策略注入拖垮对话
        logger.warning("strategy-injection hook failed: %s", exc)
        return {}


def register(ctx) -> None:
    ctx.register_hook("pre_llm_call", _pre_llm_call)
