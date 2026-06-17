# 策略内化层 (Strategy Internalization Layer)

让存进知识库的经验**真正自动影响 Agent 行为**，而非摆设。

本仓库实现了「策略注入层」：一套纯 Python 的控制平面，把工程经验提炼成轻量策略卡（YAML），在 LLM 调用前按任务信号自动匹配并注入，使 Agent 在实际任务中遵循沉淀的经验教训。

## 核心定位

控制平面（纯 Python，零 LLM）= 编译器 optimizer pass：
- 离线：长经验文 → ≤150字 strategy_card（YAML）
- 在线：按任务信号匹配 cards → 排序 → top-N → packet（token 预算内）注入 LLM

四大机制（保守注入 / 强边界隔离 / 生命周期状态机 / 负反馈日志）把注入的副作用压到最低。

## 目录

```
strategy-internalization/
├── SPEC.md                      接口契约（测试依据）
├── README.md
├── requirements.txt
├── pytest.ini
├── strategy_internalization/    实现包
│   ├── models.py                数据结构
│   ├── retriever.py             检索+闸门+状态外置+保守注入+XML边界
│   ├── signal_extractor.py      纯规则信号提取器
│   ├── lifecycle.py             卡片五态生命周期状态机
│   ├── feedback_log.py          负反馈日志（先记不学）
│   └── tokens.py                token 估算
├── cards/                       strategy_cards
│   ├── *.yaml                   active cards（注入）
│   └── shadow/                  待观察（不注入）
├── tests/                       测试（95 passed）
├── plugin/                      Hermes pre_llm_call hook 插件
│   ├── __init__.py              hook 回调（fail-open）
│   ├── plugin.yaml              插件 manifest
│   ├── enable.sh                一键启用脚本
│   └── rollback.sh              一键回滚脚本
└── scripts/
    └── call_model.py            多模型调用封装（TDD 用）
```

## 多模型 TDD 流程

1. **模型 A**（writer 角色）写测试用例（RED）
2. **模型 B**（reviewer 角色）评审，不通过打回重改，循环至通过
3. **模型 A**（executor 角色）写实现 + 跑测试至全绿（GREEN）

角色与模型映射见 `scripts/call_model.py` 的 `ROLES` 配置（填入你自己的供应商端点和 key）。

## 运行

```bash
# 克隆后进入目录
cd strategy-internalization
pip install -r requirements.txt

# 运行全量测试
pytest tests/ -v
```

## 插件接入（Hermes Agent 示例）

把 `plugin/` 内容复制到 `~/.hermes/plugins/strategy-injection/`，在 Hermes 的 `config.yaml` 的 `plugins.enabled` 加入 `strategy-injection`，重启 gateway 即可。

引擎路径通过环境变量 `STRATEGY_ENGINE_PATH` 指定，或放在插件同级/上层目录自动发现。
