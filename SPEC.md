# 策略内化层 — Phase 0 接口规格（测试契约）

> 这是 DeepSeek V4 Pro 编写测试用例的唯一依据。函数签名、返回结构、打分公式、闸门阈值均为**契约**，测试须严格据此断言。

## 1. 设计原则（不可违背）

- **控制平面 = 纯 Python，零 LLM、零 token**。所有检索/打分/闸门逻辑不调用任何模型。
- **四道硬闸门**：① 每请求检索 ≤1 次（状态外置）② 注入 cards ≤3 / packet tokens ≤800 ③ 在线禁止读原文 ④ 匹配不准降级通用策略（禁止回退翻库）。
- **状态外置**：检索记录写文件，不靠内存。

## 2. 数据结构（strategy_internalization/models.py）

```python
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class StrategyCard:
    id: str                        # 唯一标识，如 "param-no-blind-est"
    title: str                     # ≤20字
    scenario_tags: list[str]       # 8类场景子集，见 §3；可含 "general"
    trigger_keywords: list[str]    # 触发关键词（小写）
    actions: list[str]             # 可执行动作，每条 ≤40字
    priority: int                  # 1-10，10最高；基础优先级
    status: str                    # "active" | "shadow" | "archived"
    source: Optional[str] = None   # 来源（OpenViking URI）

@dataclass
class TaskSignals:
    scenario: Optional[str]        # 第0层显式场景（8类之一），None=未知需推断
    keywords: list[str]            # 任务文本提取的关键词（小写）
    text: str                      # 原始任务文本

@dataclass
class StrategyPacket:
    cards: list[StrategyCard]      # ≤3
    text: str                      # 编译后的 packet 文本（给人/LLM 看）
    tokens: int                    # packet 文本的 token 估算
    degraded: bool                 # True=降级用了通用策略
    retrieved: bool                # True=本次真检索了；False=命中状态缓存未检索
    top_scores: list[float]        # 入选 cards 的得分（诊断用）
```

## 3. 8 类任务场景（scenario_tags 取值域）

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
```

## 4. 函数签名（strategy_internalization/retriever.py）

### 4.1 加载
```python
def load_active_cards(cards_dir: str = "cards") -> list[StrategyCard]:
    """加载 cards_dir 下所有 *.yaml 中 status=="active" 的卡片。
    - 忽略 status != "active"（shadow/archived 不加载）
    - 忽略 shadow/ 子目录
    - 卡片 id 重复时抛 ValueError
    """
```

### 4.2 打分（纯函数，确定性）
```python
def score_card(card: StrategyCard, signals: TaskSignals) -> float:
    """返回 0.0-1.0 的匹配分。公式（见 §5）。"""
```

### 4.3 主入口
```python
def retrieve(
    signals: TaskSignals,
    cards_dir: str = "cards",
    state_file: str = "retrieval_state.json",
    request_id: str = "default",
    *,
    max_cards: int = 3,
    max_tokens: int = 800,
    degrade_threshold: float = 0.3,
    top_n_for_degrade_fallback: int = 3,
) -> StrategyPacket:
    """检索 + 闸门 + 状态外置。完整流程见 §6。"""
```

## 5. 打分公式（score_card 必须严格实现）

```
base = 0.0

# A. 显式场景命中（第0层）
if signals.scenario is not None and signals.scenario in card.scenario_tags:
    base += 0.30

# B. scenario_tag 关键词命中（task keywords 命中 card.scenario_tags 字面）
#    —— task keywords 中出现在 scenario_tags 取值域里的
tag_hits = len(set(signals.keywords) & set(card.scenario_tags))
base += 0.10 * tag_hits                # 每个 +0.10

# C. trigger_keywords 命中
kw_hits = len(set(signals.keywords) & set(card.trigger_keywords))
base += 0.20 * kw_hits                 # 每个 +0.20

# D. priority 加权（把 priority 映射成 0-0.20 的微调）
base += (card.priority / 10.0) * 0.20

# 归一化到 0-1
return min(base, 1.0)
```

注意：A 与 B 不叠加（A 是 scenario 字段精确命中；B 是 keywords 里恰好出现 tag 名）。两者可同时成立，分别加分。

## 6. retrieve 完整流程（闸门执行顺序）

```
1. 状态外置闸门（闸门①）
   读 state_file（JSON：{request_id: {cards_ids:[...], text, tokens, degraded, retrieved}}）
   if request_id 已存在:
       返回缓存的 packet，但 retrieved=False（标记"未实际检索"）
       —— 同一 request_id 第二次调用不再检索

2. 加载 active cards
   cards = load_active_cards(cards_dir)
   if cards 为空: 返回空 packet（retrieved=True, degraded=False, cards=[]）

3. 打分排序
   scored = [(c, score_card(c, signals)) for c in cards]
   scored.sort(key=lambda x: x[1], reverse=True)

4. 降级闸门（闸门④）
   if scored[0][1] < degrade_threshold:
       # 最高分都不达标 → 退通用策略
       general = [c for c in cards if "general" in c.scenario_tags]
       取 general 按 priority 降序前 top_n_for_degrade_fallback
       degraded=True
   else:
       取 scored 前 max_cards
       degraded=False

5. token 闸门（闸门②）
   从高到低逐张加入 packet，累计 tokens 不得超过 max_tokens
   超限则丢弃该张及之后所有（不截断单张）
   最终 cards 数量 ≤ max_cards 且 tokens ≤ max_tokens

6. 编译 packet 文本 + 写状态
   compile_packet(selected) -> text
   tokens = estimate_tokens(text)
   写 state_file[request_id] = {cards_ids, text, tokens, degraded, retrieved=True}
   返回 StrategyPacket(retrieved=True, degraded, ...)
```

## 7. token 估算（estimate_tokens）

Phase 0 用近似：`tokens ≈ len(text) / 1.5`（中英文混合的经验值）。函数：

```python
def estimate_tokens(text: str) -> int:
    return int(len(text) / 1.5) + 1
```

## 8. packet 文本格式（compile_packet）

```
## 任务相关策略卡（{N} 张）

### 1. {title}
适用: {scenario_tags}
动作:
- {action_1}
- {action_2}
优先级: P{priority}

### 2. ...
```

## 9. Card YAML 格式（cards/*.yaml）

```yaml
id: param-no-blind-est
title: 参数与资源勿盲估
scenario_tags: [ops_config, bug_fix]
trigger_keywords: [参数, 资源, 盲估, 实测, 依赖, 体积]
actions:
  - 不凭直觉设激进参数，先小范围实测
  - 确认全量传递依赖（库体积/接口）再记录
  - 建立明确质量基线后再调参
priority: 8
status: active
source: viking://agent/muge/memories/insights/策略-参数与资源勿盲估.md
```

## 10. 测试须覆盖的要点（给 DeepSeek V4 Pro 的提示）

1. **load_active_cards**: 只加载 active；shadow/archived 跳过；shadow/ 子目录跳过；id 重复抛 ValueError。
2. **score_card**: 显式场景命中 +0.30；tag 命中 +0.10/个；keyword 命中 +0.20/个；priority 加权；归一化≤1.0；零命中时仍有 priority 基底分。
3. **retrieve 正常路径**: 返回 ≤3 张；retrieved=True；按分降序；top_scores 与 cards 对应。
4. **retrieve 降级**: 最高分 < threshold → degraded=True；选中 general cards。
5. **retrieve token 闸门**: 构造 cards 使累计 token 超 800，断言被截断且 tokens ≤800。
6. **retrieve 状态外置**: 同 request_id 调两次，第二次 retrieved=False 且与第一次 packet 内容一致；不同 request_id 各自检索。
7. **retrieve 空库**: cards 为空 → 返回空 packet，retrieved=True，不崩。
8. **score_card 确定性**: 同输入多次调用结果一致。
