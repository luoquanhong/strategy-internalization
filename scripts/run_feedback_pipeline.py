#!/usr/bin/env python3
"""feedback 闭环定时执行（cron 调用）。

每天跑一次：
1. resolve_stale_exposures：把无结果曝光按时间归因（<30min→retry, >=30min→success）
2. feedback_pipeline.run_pipeline（real run）：同步负反馈 + 评估生命周期 + 写回 yaml

输出语义（no_agent 模式）：
- 有卡片状态变更 → 详细输出（作为消息发送）
- 无变更 → 简洁心跳（一行，确认系统在跑）
- 异常 → 非0退出码（触发告警）
"""
import os
import sys
from pathlib import Path
from datetime import datetime

ENGINE_PATH = Path(os.environ.get("STRATEGY_ENGINE_PATH", Path.cwd())).resolve()
sys.path.insert(0, str(ENGINE_PATH))
from strategy_internalization import experiment, feedback_pipeline, feedback_log

EXP_DB = os.environ.get("STRATEGY_EXPERIMENT_DB", str(ENGINE_PATH / "experiment.db"))
FB_DB = os.environ.get("STRATEGY_FEEDBACK_DB", str(ENGINE_PATH / "feedback.db"))
CARDS_DIR = os.environ.get("STRATEGY_CARDS_DIR", str(ENGINE_PATH / "cards"))

try:
    # 1. resolve 待定曝光
    experiment.init_db(EXP_DB)
    retry_n, success_n = experiment.resolve_stale_exposures(EXP_DB)

    # 2. 跑完整闭环（real run）
    feedback_log.init_db(FB_DB)
    report = feedback_pipeline.run_pipeline(EXP_DB, FB_DB, CARDS_DIR, dry_run=False)

    # 3. 输出
    now_str = datetime.now().strftime("%m-%d %H:%M")
    changes = report.apply_result.applied
    new_fb = report.sync_result.added

    if changes > 0:
        # 有变更 → 详细输出
        print(f"🔔 策略反馈闭环 {now_str} | {changes}张卡状态变更")
        print(f"")
        for d in report.apply_result.details:
            if d.get("applied"):
                # 查这张卡的负反馈详情
                cid = d["card_id"]
                stats = report.stats.get(cid)
                if stats:
                    print(f"  {cid}: {d['old']} → {d['new']} | "
                          f"注入{stats.total_injected} 负反馈率{stats.negative_rate:.0%} "
                          f"({stats.negative_count}次)")
                else:
                    print(f"  {cid}: {d['old']} → {d['new']}")
        if new_fb:
            print(f"")
            print(f"本轮新增负反馈 {new_fb} 条")
    else:
        # 无变更 → 简洁心跳
        active_n = sum(1 for p in Path(CARDS_DIR).glob("*.yaml")
                       if __import__("yaml").safe_load(open(p)).get("status") == "active")
        watch_n = sum(1 for p in Path(CARDS_DIR).glob("*.yaml")
                      if __import__("yaml").safe_load(open(p)).get("status") == "watch")
        print(f"✅ 策略闭环 {now_str} | {active_n}active/{watch_n}watch | "
              f"resolve +{retry_n}retry/+{success_n}success | "
              f"新负反馈{new_fb} | 0降级")

except Exception as e:
    print(f"❌ 策略闭环执行失败: {e}", file=sys.stderr)
    import traceback
    traceback.print_exc()
    sys.exit(1)
