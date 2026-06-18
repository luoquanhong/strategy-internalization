# Strategy Internalization Layer

```
strategy-internalization = muscle memory for AI Agents
—— so they stop learning the same lessons the hard way, every time
```

---

English | [中文版](#中文版)

---

## What Is This

A **pure-Python control plane** that compresses engineering experience into lightweight YAML strategy cards and injects the most relevant ones into an LLM's prompt — automatically, before each call. The Agent sees "what went wrong last time" before it writes code, fixes a bug, or touches config.

**The mental model is a compiler's optimizer pass:**
- Offline: long-form experience → compressed into ≤150-character strategy cards
- Online: before every LLM call, code picks the most relevant cards and stuffs them into the prompt

### Capabilities at a glance

| Capability | Description |
|------------|-------------|
| Strategy injection | Automatically matches the most relevant strategy cards to technical tasks (bug fix, refactor, ops config, etc.) |
| Zero-cost for chitchat | Signal extraction returns `None` → no injection, no tokens consumed |
| Strong boundary isolation | Cards wrapped in XML tags + user's original request repeated at the end to win recency — never overrides user intent |
| Conservative injection | Default cap: 2 cards / 300 tokens. Falls back to single-card mode when confidence is low |
| Fail-open | Engine exceptions are logged, never interrupt the conversation. Shield-first design |
| **Zero source-code changes** | Entirely implemented via Hermes Agent's plugin mechanism |

---

## Repository Structure

```
strategy-internalization/
├── README.md                      This file (for humans)
├── AGENTS.md                      Onboarding guide (for AI Agents)
├── SPEC.md                        Interface contract (test basis)
├── .env.example                   Environment variable template
├── requirements.txt               Python dependencies
├── pytest.ini
├── strategy_internalization/      Source package
│   ├── retriever.py               Retrieval + conservative injection + XML boundary
│   ├── signal_extractor.py        Pure-rule signal extraction
│   ├── lifecycle.py               Five-state card lifecycle (draft→active→retired)
│   ├── feedback_log.py            SQLite negative-feedback log (log first, learn later)
│   ├── models.py                  Data structures
│   └── tokens.py                  Token estimation
├── cards/                         Strategy cards
│   ├── concern-separation.yaml    9 active cards, ready to use
│   ├── ...
│   └── shadow/                    Candidate cards (promoted before injection)
├── tests/                         Tests (95 passed)
├── plugin/                        Hermes Agent plugin
│   ├── __init__.py                pre_llm_call callback
│   ├── plugin.yaml                Plugin manifest
│   ├── enable.sh                  One-click enable
│   └── rollback.sh                One-click rollback
└── scripts/
    └── call_model.py              Multi-model calling utility (for TDD)
```

---

## Quick Start (5 minutes)

### Prerequisites

- Python 3.10+
- A Hermes Agent or compatible LLM orchestration system (optional — the plugin only targets Hermes)

### Run

```bash
# Clone
git clone https://github.com/luoquanhong/strategy-internalization.git
cd strategy-internalization

# Install dependencies
pip install -r requirements.txt

# Run tests to verify (95 passed = environment is good)
pytest tests/ -v
```

---

## Two Usage Modes (read this first)

### Mode A: Pure Strategy Injection (recommended for newcomers)

**No OpenViking. No ReasoningBank.** Works out of the box.

The repo ships with 9 pre-refined active strategy cards covering bug_fix / system_design / ops_config / refactor scenarios. They can be injected into any OpenAI-compatible LLM.

#### Enable the plugin on Hermes Agent

```bash
# 1. Copy the plugin to Hermes' plugin directory
cp -r plugin $HOME/.hermes/plugins/strategy-injection

# 2. Enable (auto-backs up config + reloads)
bash $HOME/.hermes/plugins/strategy-injection/enable.sh

# 3. Verify
hermes plugins list
# You should see strategy-injection  enabled
```

> **Gateway restart deadlock**: Running `systemctl --user restart hermes-gateway` from inside a Hermes gateway session will hang — the gateway waits for the session to end, the session waits for the gateway. This is NOT a bug — after the drain timeout, the new process takes over automatically. Verify on the *next* message.

**After enabling:**
- You say "help me debug this API error" → `no-blind-bypass-error` + `param-no-blind-est` are injected automatically
- You say "let's chat" → nothing is injected (zero token overhead)

#### Using with other LLM systems

The plugin depends on Hermes' native `pre_llm_call` hook. If your orchestration system doesn't support it, you can call the core API directly:

```python
from strategy_internalization.signal_extractor import extract_signals
from strategy_internalization.retriever import retrieve, wrap_for_injection

# 1. Extract task signals
sig = extract_signals("this endpoint is returning 500")
if sig is None:
    print("chitchat, skip injection")
else:
    # 2. Retrieve strategy cards
    packet = retrieve(sig)
    # 3. Compile into injection text (with XML boundary)
    ctx = wrap_for_injection(packet.text, "this endpoint is returning 500")
    if ctx:
        print(f"Injected strategy: {ctx}")
```

---

### Mode B: Full Experience-Internalization Loop (continuous self-evolution)

> **Ideal for**: You want your Agent to continuously accumulate experience at work and automatically distill it into injectable strategies.

Requires 3 additional components:

| Component | Purpose | Installation |
|-----------|---------|-------------|
| **① OpenViking** | Knowledge base — stores raw experience (insights) | Standalone service (see its docs) |
| **② ReasoningBank** | Reasoning framework — distills experience from work traces | Standalone framework (see its docs) |
| **③ Scheduled sync job** | Scans OpenViking insights/ → generates shadow strategy cards | Hermes cronjob or standalone cron |

```
                Work traces
                   ↓
         ┌─ ReasoningBank ─┐
         │  distill insight  │
         └────────┬─────────┘
                  ↓
         ┌─ OpenViking ─────┐
         │  insights/ dir    │
         └────────┬─────────┘
                  ↓ (cron scheduled sync)
         ┌─ shadow cards ────┐
         │  observe, do NOT  │
         │  inject            │
         └────────┬─────────┘
                  ↓ (promote after stabilization)
         ┌─ active cards ────┐
         │  hook auto-injects │
         └───────────────────┘
                  ↓
         Injected before LLM call
```

**⚠️ Note**: This loop is decoupled from the strategy-internalization repo — the repo only provides the injection mechanism. It does not force any specific insight framework on you. You can produce strategy cards however you like, as long as they are valid `.yaml` files under `cards/`.

---

## Viewing Hit Statistics

```bash
# Simplest
cat retrieval_state.json | python3 -m json.tool

# Clearer summary
python3 -c "
import json, time
s = json.load(open('retrieval_state.json'))
print(f'Total records: {len(s)}')
for k in sorted(s.keys())[-20:]:
    v = s[k]
    ts = time.strftime('%m-%d %H:%M', time.localtime(v['created_at']))
    c = '+'.join([x['id'] for x in v.get('cards',[])])
    d = 'degraded' if v.get('degraded') else 'normal'
    print(f'  {ts}  [{v[\"scenario\"]:18}]  {c:45}  tok={v[\"tokens\"]}  {d}')
"
```

---

## Configuration Reference

### Engine parameters (retriever.py defaults)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_cards` | 2 | Max cards injected (high-confidence) |
| `max_tokens` | 300 | Injection text token cap |
| `high_confidence_threshold` | 0.5 | top1 ≥ 0.5 allows multi-card; below goes single-card |
| `degrade_threshold` | 0.3 | Below this, fall back to a generic strategy |

### Plugin environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `STRATEGY_ENGINE_PATH` | auto-detected | Specify engine directory |
| `STRATEGY_ENGINE_STATE` | `$HOME/strategy-internalization/retrieval_state.json` | State file path |

### Engine discovery logic (for plugins)

The engine directory is the directory that contains both the `strategy_internalization/` package and the `cards/` directory.
- `STRATEGY_ENGINE_PATH` env var takes priority
- Falls back to auto-detection: plugin sibling → parent → conventional subdirectory `strategy-internalization/`

---

## Troubleshooting

### Plugin is enabled but not hitting

```bash
# Check state file modification time
ls -la retrieval_state.json

# Check whether the plugin is actually registered
hermes plugins list | grep strategy-injection

# Manually test signal extraction
python3 -c "
from strategy_internalization.signal_extractor import extract_signals
print(extract_signals('this endpoint is erroring'))
print(extract_signals('hey there'))
"
```

### Tests are failing

```bash
# Check Python version (needs 3.10+)
python3 --version

# Check pytest is installed
python3 -m pytest --version
```

---

## Origin

This strategy-internalization layer went through a full production hardening cycle on a live Hermes Agent:
- Multi-model TDD workflow (models took turns writing tests → review → implement)
- Six-category sanitization before pushing to public GitHub
- Real-world validation: miss rate went from ≈100% (Skill soft-match) to 0% (code-level hook)
- 95 TDD tests with A/B reverse validation

It was battle-tested in production, not a proof of concept.

---

---

## 中文版

# 策略内化层（Strategy Internalization Layer）

```
策略内化层 = AI Agent 的「肌肉记忆」
—— 不让 Agent 每次都在同一个坑里现学现卖
```

---

## 这是什么

一个**纯 Python 控制平面**，把工程经验提炼成轻量 YAML 策略卡，在 LLM 调用前按任务信号自动匹配并注入。让 Agent 在写代码 / 修 bug / 配系统之前，先看到「上次踩过的坑」。

**核心逻辑像编译器的 optimizer pass**：
- 离线：长经验文 → 压缩成 ≤150 字的 strategy_card
- 在线：LLM 调用前，代码自动选最相关的卡片塞进 prompt

### 能力速览

| 能力 | 说明 |
|------|------|
| 策略注入 | 技术任务（bug fix / refactor / ops config 等）自动匹配最相关策略卡 |
| 闲聊零开销 | 提取信号返回 None → 不注入，不消耗 token |
| 强边界隔离 | XML 标签包裹卡片 + 用户请求重述吃 recency，不干扰原始意图 |
| 保守注入 | 默认最多 2 张卡 / 300 token，置信度不够自动单卡 |
| fail-open | 引擎异常只记日志不中断对话，盾牌前置 |
| **零改源码** | 全部通过 Hermes 插件机制实现 |

---

## 仓库结构

```
strategy-internalization/
├── README.md                      本文件（给人看）
├── AGENTS.md                      接入指南（给 AI Agent 看）
├── SPEC.md                        接口契约（测试依据）
├── .env.example                   环境变量模板
├── requirements.txt               Python 依赖
├── pytest.ini
├── strategy_internalization/      源码包
│   ├── retriever.py               检索 + 保守注入 + XML 边界
│   ├── signal_extractor.py        纯规则信号提取
│   ├── lifecycle.py               卡片五态生命周期（draft→active→retired）
│   ├── feedback_log.py            SQLite 负反馈日志（先记不学）
│   ├── models.py                  数据结构
│   └── tokens.py                  token 估算
├── cards/                         策略卡
│   ├── concern-separation.yaml    active 卡（9 张开箱即用）
│   ├── ...
│   └── shadow/                    候选卡（需晋升后才注入）
├── tests/                         测试（95 passed）
├── plugin/                        Hermes Agent 插件
│   ├── __init__.py                pre_llm_call 回调
│   ├── plugin.yaml                插件 manifest
│   ├── enable.sh                  一键启用
│   └── rollback.sh                一键回滚
└── scripts/
    └── call_model.py              多模型调用工具（TDD 用）
```

---

## 快速开始（5 分钟上手）

### 先决条件

- Python 3.10+
- 一个 Hermes Agent 或兼容 LLM 编排系统（可选，插件仅适配 Hermes）

### 运行

```bash
# 克隆
git clone https://github.com/luoquanhong/strategy-internalization.git
cd strategy-internalization

# 装依赖
pip install -r requirements.txt

# 跑测试验证（95 passed，证明环境正常）
pytest tests/ -v
```

---

## 两种使用方式（务必先看）

### 方式 A：纯策略注入（推荐新手）

**不需要 OpenViking、不需要 ReasoningBank。** 开箱即用。

仓库自带 9 张预先精炼的 active 策略卡（覆盖 bug_fix / system_design / ops_config / refactor 等场景），可以直接注入到任何 OpenAI 兼容的 LLM。

#### 在 Hermes Agent 上启用插件

```bash
# 1. 把插件复制到 Hermes 插件目录
cp -r plugin $HOME/.hermes/plugins/strategy-injection

# 2. 启用（自动备份 config + 重新加载）
bash $HOME/.hermes/plugins/strategy-injection/enable.sh

# 3. 验证
hermes plugins list
# 应看到 strategy-injection  enabled
```

> **网关重启死锁**：在 Hermes 网关会话内执行 restart 会卡住（gateway 等会话结束、会话等 gateway）。不是故障——等 drain 超时后新进程自动接管。验证要在下一条消息时才跑在新进程上。

**启用后效果：**
- 你说「帮我查一下这个 API 报错」→ 自动注入 no-blind-bypass-error + param-no-blind-est
- 你说「聊聊天」→ 不注入（零 token 开销）

#### 在其他 LLM 系统上使用

插件依赖 Hermes 原生的 `pre_llm_call` hook。如果你的编排系统不支持，可以：
1. 在 prompt 前手动调用 `strategy_internalization/retriever.py` 的 `retrieve()` + `wrap_for_injection()`
2. 把返回的卡片文本拼入 system prompt 或 user message 的前端

```python
from strategy_internalization.signal_extractor import extract_signals
from strategy_internalization.retriever import retrieve, wrap_for_injection

# 1. 提取任务信号
sig = extract_signals("这个接口报错 500 了")
if sig is None:
    print("闲聊，跳过注入")
else:
    # 2. 检索策略卡
    packet = retrieve(sig)
    # 3. 编译成注入文本（XML 边界包裹）
    ctx = wrap_for_injection(packet.text, "这个接口报错 500 了")
    if ctx:
        print(f"注入策略: {ctx}")
```

---

### 方式 B：完整经验内化闭环（持续自我进化）

> **适合场景**：你希望 Agent 在工作中不断积累经验，自动提炼成可注入的策略。

需要额外搭建 3 个组件：

| 组件 | 用途 | 安装方式 |
|------|------|----------|
| **① OpenViking** | 知识库，存原始经验（insights） | 独立服务（见其文档） |
| **② ReasoningBank** | 推理框架，从工作轨迹提炼经验 | 独立框架（见其文档） |
| **③ 定时同步任务** | 扫描 OpenViking insights/ → 生成 shadow 策略卡 | Hermes cronjob 或独立 cron |

```
                 工作轨迹
                    ↓
          ┌─ ReasoningBank ─┐
          │  提炼经验/策略     │
          └────────┬─────────┘
                   ↓
          ┌─ OpenViking ─────┐
          │  insights/ 目录   │
          └────────┬─────────┘
                   ↓ (cron 定时同步)
          ┌─ shadow 卡 ───────┐
          │  待观察，不注入     │
          └────────┬─────────┘
                   ↓ (稳定后晋升)
          ┌─ active 卡 ───────┐
          │  hook 自动注入     │
          └───────────────────┘
                   ↓
                LLM 调用前注入
```

**⚠️ 注意**：此闭环与策略内化层仓库解耦——仓库只提供注入机制，不强迫你用什么洞察框架。你可以用任何方式产生策略卡，只要卡片是合法的 `.yaml` 格式放 `cards/` 下就行。

---

## 查看命中统计

```bash
# 最简单
cat retrieval_state.json | python3 -m json.tool

# 更清晰的汇总
python3 -c "
import json, time
s = json.load(open('retrieval_state.json'))
print(f'总记录: {len(s)}')
for k in sorted(s.keys())[-20:]:
    v = s[k]
    ts = time.strftime('%m-%d %H:%M', time.localtime(v['created_at']))
    c = '+'.join([x['id'] for x in v.get('cards',[])])
    d = '降级' if v.get('degraded') else '正常'
    print(f'  {ts}  [{v[\"scenario\"]:18}]  {c:45}  tok={v[\"tokens\"]}  {d}')
"
```

---

## 配置说明

### 引擎参数（retriever.py 默认值）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_cards` | 2 | 最多注入卡数（高置信）|
| `max_tokens` | 300 | 注入文本 token 上限 |
| `high_confidence_threshold` | 0.5 | top1>=0.5 可走多卡；<0.5 自动单卡 |
| `degrade_threshold` | 0.3 | 低于此阈值降级到通用策略 |

### 插件环境变量

| 变量 | 默认值 | 用途 |
|------|--------|------|
| `STRATEGY_ENGINE_PATH` | 自动发现 | 指定引擎目录 |
| `STRATEGY_ENGINE_STATE` | `$HOME/strategy-internalization/retrieval_state.json` | 状态文件路径 |

### 引擎发现逻辑（给插件用）

引擎目录 = 同时包含 `strategy_internalization/` 包和 `cards/` 的目录。
- 环境变量 `STRATEGY_ENGINE_PATH` 优先
- 回退到自动探测：插件同级 → 上级 → 约定子目录 `strategy-internalization/`

---

## 故障排查

### 插件已启用但不命中
```bash
# 看状态文件最后修改时间
ls -la retrieval_state.json

# 看插件是否真的注册成功
hermes plugins list | grep strategy-injection

# 手动测试信号提取
python3 -c "
from strategy_internalization.signal_extractor import extract_signals
print(extract_signals('这个接口报错了'))
print(extract_signals('你好呀'))
"
```

### 测试不通过
```bash
# 检查 Python 版本（需要 3.10+）
python3 --version

# 检查 pytest 是否装了
python3 -m pytest --version
```

---

## 仓库起源

这套策略内化层在生产环境的 Hermes Agent 上经过了完整的：
- 多模型 TDD 流程（多模型接力写测试 → 评审 → 实现）
- 6 类脱敏后推送到公开 GitHub
- 实战验证：漏触发率从 ≈100%（Skill 软匹配）降到 0%（代码级 hook）
- 95 个 TDD 测试，A/B 反向验证

它是实际生产环境中打磨过的，不是概念验证。
