# P1 SPEC：holdout 对照 + 负反馈评分 + watch 降权注入

> GPT-5.5 评审定的 P1 三件事 + 补齐 P0-4 未接入的采集层。
> 实施方式：多模型 TDD（DeepSeek V4 Pro 写测试 → GLM-5.2 评审 → Flash 实现 → A/B 反向验证）。

## 诚实声明（必须前置）

当前规模（每天 ~10 条任务）。GPT-5.5 算过账：每天 10 × 15% holdout ≈ 每天 1.5 条对照，300 天才能做卡级因果推断。所以 P1 产不出"统计学硬结论"。价值 = 建管道 + 护栏（从盲区→有数据、新卡有验证期、坏卡有自动降权路径），不是立竿见影改善执行质量。

## 根因诊断（P1 前置）

- `feedback_log.py` 写了 log/count/recent 三函数，但 hook 从不调用 → 负反馈 db 不存在，零数据。
- `lifecycle.py` 的 `transition()` 是纯函数，无运维/cron 调用 → 卡片状态全静态 active。
- P1 的"评分""watch降权"都依赖负反馈数据，必须先补采集层。

---

## 模块 1：experiment.py（新增，sqlite 后端）

### 表结构

```sql
-- 曝光日志：每次注入或 holdout 都记一条
CREATE TABLE exposure (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id  TEXT NOT NULL,          -- get_request_id(msg)，内容+小时窗hash
    card_id     TEXT NOT NULL,
    scenario    TEXT,
    held_out    INTEGER NOT NULL DEFAULT 0,  -- 0=注入了; 1=被holdout未注入（对照组）
    timestamp   REAL NOT NULL
);
CREATE INDEX idx_exp_card ON exposure(card_id);
CREATE INDEX idx_exp_req  ON exposure(request_id);

-- 任务结果：一个 request_id 记一次结果
CREATE TABLE outcome (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id  TEXT NOT NULL,
    outcome     TEXT NOT NULL,          -- success/retry/user_corrected/tool_error
    timestamp   REAL NOT NULL
);
CREATE INDEX idx_out_req ON outcome(request_id);
```

### 常量

```python
VALID_OUTCOMES = {"success", "retry", "user_corrected", "tool_error"}
NEGATIVE_OUTCOMES = {"retry", "user_corrected", "tool_error"}  # success 不算负反馈
```

### 函数签名

```python
def init_db(db_path: str) -> None
    """幂等建两张表 + 索引。"""

def record_exposure(db_path, *, request_id, card_id, scenario=None,
                    held_out=False, timestamp=None) -> int
    """记一条曝光。held_out=True 表示对照组（本次没注入）。
    timestamp 默认 time.time()。返回新行 id。"""

def record_outcome(db_path, *, request_id, outcome, timestamp=None) -> int
    """记任务结果。outcome 不在 VALID_OUTCOMES → ValueError。返回新行 id。"""

def get_outcome(db_path, request_id) -> Optional[str]
    """返回该 request_id 的 outcome；未记录返回 None。
    一个 request_id 多次记录时取最近一条（按 timestamp 倒序）。"""

def recent_exposures_with_outcome(db_path, card_id, limit=20) -> list[dict]
    """该卡最近 limit 条【注入曝光】(held_out=0)，每条 join 对应 request_id 的 outcome。
    outcome 可能为 None（用户还没记结果）。
    返回 [{"card_id","request_id","held_out","outcome","timestamp"}, ...]
    按 timestamp 倒序。"""

def compute_card_penalty(db_path, card_id, *, window=20, threshold=0.4,
                         min_exposures=5) -> float
    """规则评分（GPT-5.5 P1-2，简单阈值非贝叶斯）。
    - 取该卡最近 window 条【注入曝光】(held_out=0)。
    - 注入曝光总数 < min_exposures → 返回 1.0（数据不足不降权，保守）。
    - 负反馈率 = (outcome in NEGATIVE_OUTCOMES 的条数) / (有 outcome 的条数)。
      （outcome 为 None 的条数不计入分母——没结果不算正也不算负。）
    - 分母为 0（都有 outcome 但全是 None 或无曝光）→ 1.0。
    - 负反馈率 >= threshold → 返回 0.5（降权因子）。
    - 否则 → 1.0。
    对照组 held_out=1 的曝光不参与评分（没注入，结果不能归因到卡）。"""

def should_holdout(card, *, now=None, holdout_probability=0.15,
                   new_card_days=7, rng=None) -> bool
    """判断该卡本次是否应 holdout（不注入做对照）。
    - card.status == "watch" → 参与holdout。
    - card.promoted_at 存在且 (now - promoted_at) < new_card_days*86400 → 参与holdout（新晋升卡）。
    - 否则（成熟 active，无 promoted_at 或已过观察期）→ False（永远注入）。
    - 参与holdout的卡：rng() < holdout_probability → True。
    - rng 默认 random.random；测试可注入确定性函数（如 lambda: 0.1）。
    - now 默认 time.time()。"""

def detect_and_log_retry(db_path, request_id, *, similarity_window=300,
                         now=None) -> bool
    """半自动重试检测（P1 负反馈采集核心）。
    - 查 exposure 表：该 request_id 之前是否已有【注入曝光】(held_out=0) 记录。
    - 有 → 说明用户又来问同一件事（重试）→ 给该 request_id 记 outcome="retry"
      （如果该 request_id 还没有 outcome 才记，避免覆盖更具体的 user_corrected）。
      返回 True。
    - 无 → 返回 False（首次请求，非重试）。
    - similarity_window 当前未使用（request_id 已含小时窗，同 request_id 即同请求），
      保留参数为未来扩展。"""
```

---

## 模块 2：lifecycle.py 改动（P1 升级）

```python
# P0: INJECTABLE_STATUSES = {"active"}          # 只有 active 注入
# P1: watch 也可降权注入（降权逻辑在 retriever）
INJECTABLE_STATUSES = {"active", "watch"}
```

`is_injectable("watch")` → True（P1 升级）。

**旧合同过期**：`test_lifecycle.py` 的 `test_only_active_injectable_in_p0` 和
`assert INJECTABLE_STATUSES == {"active"}` 需更新为 P1 语义（watch 加入），加注释
"旧合同 P0 only-active → 新合同 P1 active+watch"。

---

## 模块 3：models.py 改动

```python
@dataclass
class StrategyCard:
    id: str
    title: str
    scenario_tags: list[str]
    trigger_keywords: list[str]
    actions: list[str]
    priority: int
    status: str
    source: Optional[str] = None
    promoted_at: Optional[float] = None   # P1 新增：晋升时间戳(epoch)，用于判断新卡观察期
```

---

## 模块 4：retriever.py 改动

### 4.1 load_active_cards → 加载 active + watch

```python
def load_active_cards(cards_dir: str = "cards") -> list[StrategyCard]:
    """P1: 加载 status in {"active","watch"} 的卡（P0 只 active）。
    读 promoted_at 字段（可选，缺省 None）。
    shadow 子目录仍跳过。"""
```

### 4.2 score_card → 支持 penalty

```python
def score_card(card, signals, *, penalty=1.0) -> float:
    """P1: base 分 × penalty（penalty 来自 compute_card_penalty，默认1.0不降权）。
    原打分逻辑不变：scenario命中+0.30 / tag命中+0.10 / kw命中+0.20 / priority+0.20。
    返回 min(base * penalty, 1.0)。"""
```

### 4.3 retrieve 新增参数 + P1 流程

```python
def retrieve(signals, cards_dir="cards", state_file="retrieval_state.json",
             request_id="default",
             *, max_cards=2, max_tokens=300, degrade_threshold=0.3,
             high_confidence_threshold=0.5, top_n_for_degrade_fallback=3,
             ttl_seconds=0,
             experiment_db=None,           # P1: 传入则启用 holdout+penalty+曝光
             holdout_probability=0.15,
             _rng=None,                    # 测试注入 should_holdout 的 rng
             _now=None                     # 测试注入 should_holdout 的 now
             ) -> StrategyPacket:
```

**experiment_db=None 时**：完全走老逻辑（零回归，现有测试不受影响）。

**experiment_db 传入时**，在原流程中插入 P1 逻辑：

1. state gate（防重入）—— 不变。命中缓存时直接返回（不重复记曝光）。
2. load active+watch 卡。
3. 对每张卡算 `penalty = compute_card_penalty(experiment_db, card.id)`（experiment_db 传入时）。
4. `score = score_card(card, signals, penalty=penalty)`。
5. **holdout 过滤**：对每张候选卡调 `should_holdout(card, rng=_rng, now=_now, holdout_probability=...)`。
   - True → 该卡移出候选，`record_exposure(held_out=True)`（对照组）。
   - holdout 后候选为空 → 返回空 packet（不降级翻库，不注入）。
6. degrade gate —— 不变（degraded 走 general 兜底，不走 holdout/penalty）。
7. **保守注入 + watch 单卡模式**：
   - 非 degraded 路径：relevant_pairs（score >= degrade_threshold）排序。
   - 如果 top1 是 watch 卡 → **单卡模式**：只注入这 1 张 watch 卡（不叠加 active 卡）。
     且 watch 卡 score 需 >= high_confidence_threshold 才注入（否则不注入，保守）。
   - top1 是 active 卡 → 走原保守注入（高置信≤max_cards / 中置信单卡）。
8. token gate —— 不变。
9. write state —— 不变。
10. **记曝光**：对每张最终注入的卡 `record_exposure(held_out=False)`（experiment_db 传入时）。

---

## 模块 5：hook 接入（__init__.py）

`_pre_llm_call` 改动：

1. 新增实验数据库路径常量，推荐由 `STRATEGY_ENGINE_PATH` 推导：`_DB = os.path.join(_ENGINE_PATH, "experiment.db")`。
2. retrieve 调用传 `experiment_db=_DB`。
3. retrieve 之前调 `detect_and_log_retry(_DB, request_id)`（半自动重试检测 → 给上次注入记 retry 负反馈）。
4. retrieve 之后（注入成功时），曝光已在 retrieve 内部记了（experiment_db 传入时）。
   hook 不重复记。
5. `init_db(_DB)` 在模块加载或首次调用时幂等执行。

---

## 测试覆盖要求（给 Pro）

### experiment.py 单元测试
- init_db 幂等（重复调用不报错）
- record_exposure / record_outcome 基本读写 + held_out 标记
- record_outcome 非法 outcome 抛 ValueError
- get_outcome 未记录返回 None；多次记取最近
- recent_exposures_with_outcome 只返回 held_out=0 的 + join outcome（含 None 情况）
- compute_card_penalty：曝光不足→1.0；全成功→1.0；负反馈率>=0.4→0.5；边界(threshold恰等)→0.5；对照组不参与
- should_holdout：成熟active→False；watch→按概率；新卡(promoted_at 3天内)→按概率；新卡过观察期→False；rng注入确定性
- detect_and_log_retry：首次→False；第二次→True+记retry；已有user_corrected不覆盖

### lifecycle.py 更新测试
- INJECTABLE_STATUSES == {"active","watch"}
- is_injectable("watch") == True（旧合同过期，更新断言+注释）

### retriever.py 集成测试
- experiment_db=None：行为同老逻辑（回归保护）
- experiment_db传入 + 成熟active卡：正常注入 + 记曝光(held_out=0)
- experiment_db传入 + watch卡：单卡模式（不叠加）+ 阈值门控
- holdout：rng注入0.1→新卡被holdout+记对照组曝光；rng注入0.9→不holdout
- penalty：构造负反馈数据→卡被降权→可能跌出候选
- holdout后候选空→返回空packet
- 防重入：同request_id第二次返回缓存，不重复记曝光

### hook 集成测试（test_plugin_hook.py 补充）
- experiment_db 传入 retrieve
- detect_and_log_retry 在 retrieve 前调用
- 注入成功后曝光已记

## A/B 反向验证要求
- BUG版（experiment.py/retriever改动不存在）→ P1 新测试精准 RED
- 修复版 → 全量（含原95）GREEN
- holdout 真分流验证：rng=0.1 时曝光表有 held_out=1 记录
- penalty 真降权验证：构造负反馈→该卡 score 真降
