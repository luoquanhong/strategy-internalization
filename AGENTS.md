# AGENTS.md — Strategy Internalization Onboarding Guide (for AI Agents)

> This document is for AI Agents (Hermes, Claude Code, or other LLM orchestration systems) to read when they receive tasks like "integrate strategy injection" or "make the Agent auto-apply experience."

---

English | [中文版](#中文版)

---

## What This Is, and What Pain It Solves

**The pain**: Experience, lessons learned, and best practices are already written down in docs, issues, and post-mortems — but when the Agent is doing real work, it **never actively remembers to use them**. It learns the same lesson the hard way, every single time.

**This project's solution**: Compress experience into lightweight cards, then automatically select the most relevant ones and stuff them into the prompt **before** the LLM call — using code. The Agent has no "skip" option — this is a code-level hook, not relying on the Agent's memory or willpower.

### Core Mechanism (required reading)

This is **control-plane** thinking, not a memory system:

```
Wrong mental model:   store experience → Agent "remembers" → recalls it like a human
Correct mental model: offline pre-compile experience into short cards →
                       before every call, code picks a few and stuffs them in
```

The Agent only consumes finished cards. It does not search a knowledge base online, nor does it decide how many items to read. This avoids two fatal problems:
1. **Context death spiral** (letting an LLM read a knowledge base online = infinite money burn)
2. **Growth rot** (experience grows without bound, but the amount injected into the main context stays capped and controllable)

---

## Two Usage Scenarios (decide which one you are)

### Scenario A: Pure Strategy Injection (default, recommended)

**Goal**: Let the Agent see distilled experience cards before technical tasks.

**Prerequisites**:
- Python 3.10+
- Hermes Agent (the plugin depends on Hermes' native `pre_llm_call` hook; other systems need to implement an equivalent hook themselves)
- **No** OpenViking, **no** ReasoningBank required

**Why**: The repo ships with 9 battle-hardened active strategy cards (covering bug_fix / system_design / ops_config / refactor etc.), ready to use out of the box.

### Scenario B: Full Experience-Internalization Loop

**Goal**: Let the Agent continuously accumulate experience at work and automatically distill it into new strategy cards.

**Prerequisites**: Everything from Scenario A, plus these 3 components:

| Component | Responsibility | Key Output |
|-----------|---------------|------------|
| OpenViking | Knowledge base | Long-form experience articles in the insights/ directory |
| ReasoningBank | Reasoning framework | Distills generalizable strategies from work traces |
| Scheduled sync job | Cron / Hermes job | Scans insights/ → generates shadow cards |

**Full data flow**:
```
Work traces → ReasoningBank inference → OpenViking insights/
   → (cron scheduled sync) → shadow cards (observed, NOT injected)
   → (promote after stabilization) → active cards → hook auto-injects
```

**⚠️ Critical note**: This repo is **only responsible for the final step** (active cards → injection). OpenViking, ReasoningBank, and the cron sync job are all upstream and NOT part of this repo. You can produce strategy cards however you like, as long as they are valid `.yaml` files placed under `cards/`.

---

## Integrating with Hermes Agent (Scenario A step-by-step)

### Step 1: Clone the repo

```bash
git clone https://github.com/luoquanhong/strategy-internalization.git
cd strategy-internalization
pip install -r requirements.txt
pytest tests/ -v   # should show 95 passed
```

### Step 2: Install the plugin

```bash
# Copy the plugin to Hermes' plugin directory
mkdir -p $HOME/.hermes/plugins
cp -r plugin $HOME/.hermes/plugins/strategy-injection
```

### Step 3: Enable the plugin

```bash
# Option 1: Use the provided script (recommended — auto-backs up config)
bash $HOME/.hermes/plugins/strategy-injection/enable.sh

# Option 2: Manually edit config.yaml
# In ~/.hermes/config.yaml, add:
#   plugins:
#     enabled:
#       - strategy-injection
# Then: systemctl --user restart hermes-gateway
```

### Step 4: Verify

```bash
# Plugin status
hermes plugins list | grep strategy-injection
# Should show "enabled"

# Send a technical message (e.g. "help me debug this API error")
# then check the state file
python3 -c "
import json
s = json.load(open('retrieval_state.json'))
print(f'Records: {len(s)}')
for k,v in list(s.items())[-3:]:
    print(f'  {v.get(\"scenario\")}: {[c[\"id\"] for c in v.get(\"cards\",[])]}')
"
```

### ⚠️ Gateway restart deadlock (pitfall alert)

Running `systemctl --user restart hermes-gateway` from inside a Hermes gateway session will hang — the gateway waits for the current session to end, while the session waits for the gateway. **This is not a bug**: after the drain timeout (default 180s), the new process takes over automatically.

**Correct approach**: After issuing the restart, wait a few seconds and let the user send the *next* message. That message will run on the new process and you can verify the hook is active. The current message cannot verify itself.

---

## The Card System (what an Agent needs to know for debugging)

### Card format (cards/*.yaml)

```yaml
id: param-no-blind-est                  # unique identifier
title: Don't Blind-Estimate Parameters  # ≤20 chars
scenario_tags: [ops_config, bug_fix]    # scenario (determines matching)
trigger_keywords: [parameter, resource, estimate, measure]  # trigger keywords
actions:                                  # executable actions
  - Don't set aggressive parameters on intuition; benchmark first
  - Verify full transitive dependencies before recording
priority: 8                               # 1-10, 10 highest
status: active                            # active | shadow
source: viking://.../some_insight.md      # provenance (not read at runtime)
```

### Card states (lifecycle.py five-state machine)

```
draft → active → watch → quarantine → retired
```

**Only cards with `status: active` are injected**. Cards in the `shadow/` subdirectory are never loaded (candidate observation zone).

### Scenario tags (8 categories)

```
new_build              New implementation
bug_fix                Bug fix
refactor               Existing refactor
test_validation        Test validation
security_sanitization  Security sanitization
ops_config             Ops configuration
code_review            Code review
doc_comms              Documentation / communication
general                General (used on degradation)
system_design          System design (project extension)
```

### Signal extraction (signal_extractor.py)

**Pure rules, zero LLM**. Converts a user message into `{scenario, keywords, text}` or `None` (chitchat).

**Key design**: Maintains a `STRONG_SIGNALS` allowlist (error / model / config / performance keywords) — these words trigger on a **single occurrence**, bypassing the keyword-hit threshold. Weak signals only trigger when enough accumulate.

**Avoiding false positives**:
- "this endpoint is returning 500" → hits strong signal "error" → triggers ✓
- "you're replying too slow" → weak signal "slow" alone → does not trigger ✓

---

## Debugging & Troubleshooting

### Hook not hitting

```bash
# 1. Does the state file have new records? If not → hook never truly fired
ls -la retrieval_state.json

# 2. Manually test signal extraction
python3 -c "
from strategy_internalization.signal_extractor import extract_signals
print(extract_signals('this endpoint is returning 500'))   # should be non-None
print(extract_signals('nice weather today'))                # should be None
"

# 3. Check whether the plugin actually registered
hermes plugins list | grep strategy-injection
```

### Fail-open verification

When the engine throws, the hook must return an empty dict without interrupting the conversation:
```python
from strategy_internalization import signal_extractor as se
# Simulate engine crash
se.extract_signals = lambda *a,**k: (_ for _ in ()).throw(RuntimeError("boom"))
# Calling _pre_llm_call should return {} rather than raising
```

### Viewing hit history

```bash
python3 -c "
import json, time
s = json.load(open('retrieval_state.json'))
for k in sorted(s.keys()):
    v = s[k]
    ts = time.strftime('%m-%d %H:%M', time.localtime(v['created_at']))
    c = '+'.join([x['id'] for x in v.get('cards',[])])
    print(f'{ts} [{v[\"scenario\"]:18}] {c:40} tok={v[\"tokens\"]}')
"
```

---

## File Responsibility Quick Reference

| File | Responsibility | When to read |
|------|---------------|-------------|
| `strategy_internalization/retriever.py` | Retrieval + conservative injection + XML boundary | Modifying injection logic |
| `strategy_internalization/signal_extractor.py` | Pure-rule signal extraction | Tuning matching / adding scenario keywords |
| `strategy_internalization/lifecycle.py` | Five-state card lifecycle | Card promotion / quarantine |
| `strategy_internalization/feedback_log.py` | Negative-feedback log | Future scoring (currently log-only, no learning) |
| `plugin/__init__.py` | pre_llm_call callback | Changing hook behavior |
| `cards/*.yaml` | Active card data | Adding / modifying strategies |
| `cards/shadow/*.yaml` | Candidate cards | Observe before promotion |
| `scripts/call_model.py` | Multi-model TDD utility | Multi-model collaboration for card creation |

---

## FAQ

**Q: Do I still need the strategy-retrieval Skill after installing the hook?**
A: No. The hook is a code-level hard-wired entry point; the Skill is soft matching (observed miss rate ≈100%). After installing the hook, the Skill becomes redundant — keep it as a "troubleshooting manual" if you want.

**Q: Will chitchat get strategy cards injected?**
A: No. `extract_signals` returns `None` for chitchat. The hook returns an empty dict — zero token overhead.

**Q: Can I use this without Hermes?**
A: Yes. The core (retriever / signal_extractor) is pure Python with no Hermes dependency. Only `plugin/` depends on Hermes' `pre_llm_call` hook. Other systems just need to implement an equivalent hook.

**Q: If I add a new card under cards/, does it take effect immediately?**
A: A new card with `status: active` placed under `cards/` (not shadow/) will be loaded the next time the hook fires. Cards in the `shadow/` subdirectory are never loaded.

**Q: How do I turn this off?**
A: `bash $HOME/.hermes/plugins/strategy-injection/rollback.sh` (soft rollback — keeps plugin files). Add `--clean` to completely delete the plugin + clear cache.

**Q: I want to create my own cards. Where does the content come from?**
A: Scenario A (pure injection): write YAML directly by hand, following the format of the 9 existing active cards. Scenario B (closed loop): recommended approach is to use ReasoningBank to distill from work traces, then compress into ≤150-character cards.

---

## Anti-Patterns (Do NOT Do This)

- ❌ Let an LLM read OpenViking long-form articles online for "experience retrieval" (infinite money-burn death spiral)
- ❌ Set a uniform hit threshold for all keywords (strong-signal words like "error" / "model" must have individual exemptions)
- ❌ Turn every lesson into a Skill (Skills bloat the main context — they must remain scarce)
- ❌ Depend on the LLM "remembering it already searched" (state must be externalized to files)
- ❌ On a mismatch, "search the library again for a better match" (death spiral entry point)
- ❌ Set a new strategy card to `active` immediately (put it in shadow first, observe, then promote once stable)

---

## Relationship to Other Systems

- **OpenViking**: Knowledge base. This repo's card `source` field points to OpenViking insights (provenance, not a runtime dependency).
- **ReasoningBank**: Upstream reasoning framework. Source of card content (needed for Scenario B closed loop).
- **Hermes Agent**: Plugin host. This repo's `plugin/` directory depends on its `pre_llm_call` hook.

This repo sits at the **very tail end** of the closed loop — injecting active cards into the LLM. Whatever insight framework or knowledge base you use upstream, it works, as long as the final output is valid YAML cards.

---

---

## 中文版

# AGENTS.md — Strategy Internalization 接入指南（面向 AI Agent）

> 本文件供 AI Agent（Hermes / Claude Code / 其他 LLM 编排系统）阅读，指导如何接入和使用策略内化层。Agent 收到「集成策略注入」「让 Agent 自动应用经验」类任务时先读这个。

---

## 这是什么，解决什么痛点

**痛点**：经验/教训/最佳实践已经写在文档、Issue、复盘里，但 Agent 干活时**根本不会主动想起来用**。每次都在同一个坑里现学现卖。

**本项目的解决方案**：把经验压缩成轻量卡片，在 LLM 调用**之前**用代码自动挑最相关的塞进 prompt。Agent 没有「跳过」选项——这是代码级 hook，不是靠 Agent 自觉。

### 核心机制（必读）

这是**控制平面**思维，不是记忆系统：

```
传统错误心智：经验存进去 → Agent "记住" 了 → 像人一样想起来
正确心智：    经验离线预编译成短卡 → 每次调用前，代码挑几张塞进 prompt
```

Agent 只消费成品卡片，不在线翻库、不自己决定读几条。这避免两个致命问题：
1. **上下文死循环**（让 LLM 在线读库 = 烧钱无底洞）
2. **增长腐烂**（经验无限增长，但注入主上下文的量恒定可控）

---

## 两种使用场景（先判断你属于哪种）

### 场景 A：纯策略注入（默认推荐）

**目标**：让 Agent 在技术任务前看到沉淀的经验卡。

**先决条件**：
- Python 3.10+
- Hermes Agent（插件依赖 Hermes 原生 `pre_llm_call` hook；其他系统需自己实现等价 hook）
- **不需要** OpenViking，**不需要** ReasoningBank

**为什么**：仓库自带 9 张经过实战打磨的 active 策略卡（覆盖 bug_fix / system_design / ops_config / refactor 等），开箱即用。

### 场景 B：完整经验内化闭环

**目标**：让 Agent 在工作中持续积累经验，自动提炼成新策略卡。

**先决条件**：场景 A 全部 + 以下 3 个组件：

| 组件 | 职责 | 关键产出 |
|------|------|----------|
| OpenViking | 知识库 | insights/ 目录里的长篇经验文 |
| ReasoningBank | 推理框架 | 从工作轨迹提炼可泛化策略 |
| 定时同步任务 | cron / Hermes job | 扫描 insights/ → 生成 shadow 卡 |

**完整数据流**：
```
工作轨迹 → ReasoningBank 推理 → OpenViking insights/
   → (cron 定时同步) → shadow 卡（不注入，观察中）
   → (稳定后晋升) → active 卡 → hook 自动注入
```

**⚠️ 关键提醒**：本仓库**只负责最后一步**（active 卡 → 注入）。上游的 OpenViking / ReasoningBank / cron 同步任务都不在本仓库内。你可以用任何方式产生策略卡，只要卡片是合法 `.yaml` 放进 `cards/` 目录。

---

## 接入 Hermes Agent（场景 A 详细步骤）

### 第 1 步：克隆仓库

```bash
git clone https://github.com/luoquanhong/strategy-internalization.git
cd strategy-internalization
pip install -r requirements.txt
pytest tests/ -v   # 应 95 passed
```

### 第 2 步：安装插件

```bash
# 复制插件到 Hermes 插件目录
mkdir -p $HOME/.hermes/plugins
cp -r plugin $HOME/.hermes/plugins/strategy-injection
```

### 第 3 步：启用插件

```bash
# 方式一：用现成脚本（推荐，自动备份 config）
bash $HOME/.hermes/plugins/strategy-injection/enable.sh

# 方式二：手动改 config.yaml
# 在 ~/.hermes/config.yaml 加：
#   plugins:
#     enabled:
#       - strategy-injection
# 然后: systemctl --user restart hermes-gateway
```

### 第 4 步：验证

```bash
# 插件状态
hermes plugins list | grep strategy-injection
# 应显示 enabled

# 发一条技术消息（如 "帮我查这个 API 报错"），看是否命中
# 命中后检查状态文件
python3 -c "
import json
s = json.load(open('retrieval_state.json'))
print(f'记录数: {len(s)}')
for k,v in list(s.items())[-3:]:
    print(f'  {v.get(\"scenario\")}: {[c[\"id\"] for c in v.get(\"cards\",[])]}')
"
```

### ⚠️ 网关重启死锁（踩坑提醒）

从 Hermes 网关**会话内**执行 `systemctl --user restart hermes-gateway` 会卡住——gateway 等当前会话结束，会话又等 gateway。**这不是故障**：drain 超时后（默认 180s）新进程自动接管。

**正确做法**：执行重启后，等几秒让用户发下一条消息，那条消息才会跑在新进程上验证 hook 是否生效。当条消息无法自证。

---

## 卡片系统（Agent 调试时要知道）

### 卡片格式（cards/*.yaml）

```yaml
id: param-no-blind-est                  # 唯一标识
title: 参数与资源勿盲估                   # ≤20 字
scenario_tags: [ops_config, bug_fix]     # 场景标签（决定匹配场景）
trigger_keywords: [参数, 资源, 盲估, 实测] # 触发关键词
actions:                                  # 可执行动作
  - 不凭直觉设激进参数，先小范围实测
  - 确认全量传递依赖再记录
priority: 8                               # 1-10，10最高
status: active                            # active | shadow
source: viking://.../某insight.md         # 溯源（运行时不读）
```

### 卡片状态（lifecycle.py 五态）

```
draft → active → watch → quarantine → retired
```

**只有 `active` 状态的卡片会被注入**。`shadow/` 子目录的卡片永远不加载（候选观察区）。

### 场景标签（8 类）

```
new_build              新建实现
bug_fix                缺陷修复
refactor               既有改造
test_validation        验证测试
security_sanitization  安全脱敏
ops_config             运维配置
code_review            代码评审
doc_comms              文档沟通
general                通用（降级时用）
system_design          系统设计（项目扩展）
```

### 信号提取（signal_extractor.py）

**纯规则，零 LLM**。把用户消息转成 `{scenario, keywords, text}` 或 None（闲聊）。

**关键设计**：维护 `STRONG_SIGNALS` 白名单（报错 / 模型 / 配置 / 性能等强信号词）——这类词**单独出现 1 次就触发**，不受关键词命中阈值约束。弱信号词才需要凑够阈值。

**避免误判**：
- 「这个接口报错了」→ 命中强信号「报错」→ 触发 ✓
- 「你回复好慢」→ 弱信号「慢」单字 → 不触发 ✓

---

## 调试与排查

### Hook 不命中

```bash
# 1. 状态文件有新记录吗？没有 = hook 没真触发
ls -la retrieval_state.json

# 2. 手动测信号提取
python3 -c "
from strategy_internalization.signal_extractor import extract_signals
print(extract_signals('这个接口报错 500 了'))   # 应非 None
print(extract_signals('今天天气真好'))           # 应 None
"

# 3. 检查插件是否真注册成功
hermes plugins list | grep strategy-injection
```

### Fail-open 验证

引擎抛异常时，hook 必须返回空 dict 不中断对话：
```python
from strategy_internalization import signal_extractor as se
# 模拟引擎挂掉
se.extract_signals = lambda *a,**k: (_ for _ in ()).throw(RuntimeError("boom"))
# 调用 _pre_llm_call 应返回 {} 而不是抛异常
```

### 查命中历史

```bash
python3 -c "
import json, time
s = json.load(open('retrieval_state.json'))
for k in sorted(s.keys()):
    v = s[k]
    ts = time.strftime('%m-%d %H:%M', time.localtime(v['created_at']))
    c = '+'.join([x['id'] for x in v.get('cards',[])])
    print(f'{ts} [{v[\"scenario\"]:18}] {c:40} tok={v[\"tokens\"]}')
"
```

---

## 文件职责速查

| 文件 | 职责 | 何时读 |
|------|------|--------|
| `strategy_internalization/retriever.py` | 检索 + 保守注入 + XML 边界 | 改注入逻辑 |
| `strategy_internalization/signal_extractor.py` | 纯规则信号提取 | 调匹配 / 加场景关键词 |
| `strategy_internalization/lifecycle.py` | 卡片五态流转 | 卡片晋升 / 隔离 |
| `strategy_internalization/feedback_log.py` | 负反馈日志 | 后续做评分（当前只记不学）|
| `plugin/__init__.py` | pre_llm_call 回调 | 改 hook 行为 |
| `cards/*.yaml` | active 卡数据 | 加 / 改策略 |
| `cards/shadow/*.yaml` | 候选卡 | 晋升前观察 |
| `scripts/call_model.py` | 多模型 TDD 工具 | 多模型协作写卡时用 |

---

## 常见问题

**Q: 用了 hook 还需要装 strategy-retrieval Skill 吗？**
A: 不需要。hook 是代码级焊死入口，Skill 是软匹配（实测漏触发率 ≈100%）。装 hook 后 Skill 变冗余，可保留作「故障手册」。

**Q: 闲聊会被注入策略卡吗？**
A: 不会。`extract_signals` 对闲聊返回 None，hook 返回空 dict，零 token 开销。

**Q: 我不想用 Hermes，能用其他系统吗？**
A: 能。本仓库核心（retriever / signal_extractor）是纯 Python，不依赖 Hermes。只有 `plugin/` 目录依赖 Hermes 的 `pre_llm_call` hook。其他系统自己实现等价 hook 即可。

**Q: cards/ 下加新卡就立刻生效吗？**
A: 新卡 `status: active` 放 `cards/`（非 shadow/）后，下次 hook 触发就会加载。shadow 子目录的卡永远不加载。

**Q: 怎么关掉这个功能？**
A: `bash $HOME/.hermes/plugins/strategy-injection/rollback.sh`（软回滚，保留插件文件）。加 `--clean` 彻底删除插件 + 清缓存。

**Q: 想自己造卡片，卡片内容从哪来？**
A: 场景 A（纯注入）可以直接手写 YAML，参考现有 9 张 active 卡的格式。场景 B（闭环）的话，推荐用 ReasoningBank 从工作轨迹推理提炼，再压缩成 ≤150 字的卡片。

---

## 反模式（不要做）

- ❌ 让 LLM 在线读 OpenViking 长文做「经验检索」（死循环烧钱）
- ❌ 给所有关键词设统一命中阈值（强信号词如「报错」「模型」必须单独豁免）
- ❌ 把每条经验都升成 Skill（Skill 会膨胀主上下文，必须稀缺）
- ❌ 依赖 LLM「记得自己检索过」（必须状态外置到文件）
- ❌ 匹配不准时「再翻一次库找更好的」（死循环入口）
- ❌ 新策略卡一上来就 `active`（先进 shadow 观察，稳定再晋升）

---

## 与其他系统的关系

- **OpenViking**：知识库。本仓库的卡片 `source` 字段指向 OpenViking insights（溯源，非运行时依赖）
- **ReasoningBank**：上游推理框架。本仓库的卡片内容来源（场景 B 闭环需要）
- **Hermes Agent**：插件宿主。本仓库的 `plugin/` 目录依赖其 `pre_llm_call` hook

本仓库在闭环中的位置：**最末端——把 active 卡注入 LLM**。上游用什么洞察框架、什么知识库，都行，只要最终产出的卡片是合法 YAML。
