# P1 实现完成报告 — holdout对照 + 负反馈评分 + watch注入

**日期**: 2026-06-18
**状态**: ✅ 实现完成，143测试全绿，A/B反向验证通过，烟雾测试6/6通过
**待激活**: 需重启 Hermes Gateway 加载新 hook 插件代码

---

## 一、P1 做了什么

从"能注入卡片"升级到"验证注入有没有用"的价值闭环：

1. **Holdout 对照验证**：15% 的 watch 卡和新 active 卡（promoted < 7天）被随机分流到对照组（不注入），记录 `held_out=1` 曝光。注入的卡记录 `held_out=0`。后续可对比两组 outcome。

2. **负反馈评分降权**：`compute_card_penalty()` 基于最近 20 次 held_out=0 曝光的 outcome 计算惩罚值。负反馈率（retry/user_corrected/tool_error）≥ 40% 且 ≥ 5 条曝光 → penalty=0.5，否则 1.0。penalty 乘到 score_card 的 base score 上。

3. **Watch 卡注入**：`load_active_cards()` 从只加载 "active" 扩展为加载 {"active", "watch"}。Watch 卡是实验性卡，需达到 `high_confidence_threshold` 才注入（单卡模式，不叠加 active）。

4. **曝光+outcome 记录**：hook 插件在 `pre_llm_call` 时传入 `experiment_db`，retrieve() 自动记录曝光。`mark_stale_exposures_as_retry()` 在每次 hook 调用时标记超时（>300s）无 outcome 的曝光为 retry。

## 二、关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 零回归方式 | `experiment_db: Optional[str] = None` 默认 None | 现有 retrieve() 调用不传此参数时行为完全不变 |
| Holdout 生效条件 | watch 卡 或 promoted_at < 7天的新 active 卡 | 成熟卡不需要对照，已有足够历史数据 |
| Holdout 概率 | 15% | 当前规模 ~10条/天，15% → ~1.5条/天对照 |
| Penalty 阈值 | 负反馈率 ≥ 40% 且 ≥ 5 条曝光 | 双门槛防小样本误杀 |
| Penalty 值 | 0.5（降权）或 1.0（正常） | 简单二值，不做连续降权 |
| Watch 卡门槛 | score ≥ high_confidence_threshold (0.5) | 实验性卡需更高置信度才注入 |
| Retry 检测 | 超时 >300s 无 outcome → 推定 retry | hook 无法直接获取 LLM 响应质量，用时间窗口近似 |

## 三、改动文件清单

| 文件 | 改动 |
|------|------|
| `strategy_internalization/experiment.py` | 新增 `mark_stale_exposures_as_retry()`；`recent_exposures_with_outcome()` 加 `include_held_out` 参数 |
| `strategy_internalization/models.py` | `StrategyCard` 加 `promoted_at` 字段；`StrategyPacket` 加 `cards_ids` property |
| `strategy_internalization/retriever.py` | `load_active_cards()` 加载 watch 卡 + promoted_at；`score_card()` 加 penalty 参数；`retrieve()` 加 experiment_db/_rng/_now 参数 + holdout 分流 + penalty 降权 + watch 门槛 + 曝光记录 |
| `strategy_internalization/lifecycle.py` | `INJECTABLE_STATUSES` 从 {"active"} → {"active", "watch"} |
| `~/.hermes/plugins/strategy-injection/__init__.py` | hook 接入 experiment_db + init_db + mark_stale_exposures_as_retry |
| `tests/test_p1_integration.py` | 新增 10 个集成测试 |
| `tests/test_experiment.py` | 新增 `test_mark_stale_exposures_as_retry` |
| `tests/test_plugin_hook.py` | 新增 3 个 P1 hook 集成测试 |
| `tests/test_lifecycle.py` | 适配 P1 新合同 |

## 四、测试统计

- **总测试数**: 143 passed
- **P1 新增测试**: 14 个（10 集成 + 1 experiment + 3 hook）
- **A/B 反向验证**: BUG版10红 → 修复版10绿 ✅
- **烟雾测试**: 6/6 场景通过 ✅

## 五、实施中遇到的坑

1. **Pro 臆造 API**：Pro 写测试时臆造了 `packet.cards_ids`（不存在）、`e.held_out`（dict 不是对象）、`score_default`（未定义变量）。修复：加 property、改 dict 访问、删未定义变量。

2. **should_holdout 签名误导**：给 Flash 的 prompt 中 API 签名写错（写成 `(card_id, db_path)`，实际是 `(card, *, now, rng)`）。Flash 按错误签名实现，导致 TypeError。修复：改为传 card 对象。

3. **空卡检查遗漏**：Flash 把 `if not all_cards:` 检查移到了 experiment block 内部，导致 `experiment_db=None` + 空目录时跳过检查，`scored[0]` 爆 IndexError。修复：在 load 后加回空检查。

4. **Watch 卡门槛逻辑缺失**：Flash 只实现了 watch 卡单卡模式，但没实现"watch 卡分数 < high_confidence_threshold 时不注入"。补了 watch gate 逻辑。

5. **recent_exposures_with_outcome 硬编码过滤**：原函数硬编码 `WHERE held_out = 0`，但测试需要查 held_out=1 的曝光。加了 `include_held_out` 参数。

## 六、诚实声明

当前规模 ~10 条/天，holdout 15% → 每天 ~1.5 条对照。P1 产不出统计学硬结论，价值在建管道+护栏。后续需要积累数据到一定规模后才能做 holdout 对照的统计分析。

## 七、后续待办

- [ ] 重启 Hermes Gateway 激活新 hook 插件
- [ ] 运行一段时间后检查 experiment.db 数据
- [ ] P2: shadow 卡审计清退（与 P1 可并行）
- [ ] P2: 基于 holdout 数据做 A/B 对照统计分析（需积累数据）
