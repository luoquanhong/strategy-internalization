"""shadow 池配额检查器（F, P10）。

防止 cron 同步无限产出 shadow 卡导致池膨胀。
cron 同步前调用 check_quota，超限的场景/全局跳过新增。
"""
from dataclasses import dataclass
from pathlib import Path
import yaml

PER_SCENARIO_LIMIT = 10   # 每个场景 shadow 卡上限
GLOBAL_LIMIT = 50          # 全局 shadow 卡上限


@dataclass
class QuotaReport:
    per_scenario: dict       # 场景 → shadow 卡数
    total: int               # shadow 卡总数
    per_scenario_limit: int
    global_limit: int

    @property
    def over_scenarios(self) -> list:
        """已达场景上限的场景列表。"""
        return [s for s, n in self.per_scenario.items()
                if n >= self.per_scenario_limit]

    @property
    def over_global(self) -> bool:
        """全局总数已达上限。"""
        return self.total >= self.global_limit

    @property
    def can_add_more(self) -> bool:
        """全局还能不能再加 shadow 卡。"""
        return not self.over_global

    def can_add_to(self, scenario: str) -> bool:
        """指定场景还能不能再加一张 shadow 卡（全局未满且该场景未满）。"""
        return (not self.over_global
                and self.per_scenario.get(scenario, 0) < self.per_scenario_limit)


def check_quota(shadow_dir, per_scenario_limit=PER_SCENARIO_LIMIT,
                global_limit=GLOBAL_LIMIT) -> QuotaReport:
    """扫描 shadow 目录，返回配额报告。只数 status=shadow 的卡。

    Args:
        shadow_dir: shadow 卡所在目录（如 cards/shadow）
        per_scenario_limit: 单场景上限
        global_limit: 全局上限
    """
    shadow_dir = Path(shadow_dir)
    per_scenario: dict = {}
    total = 0
    for fp in sorted(shadow_dir.glob("*.yaml")):
        with open(fp) as f:
            data = yaml.safe_load(f) or {}
        if data.get("status") != "shadow":
            continue
        tags = data.get("scenario_tags") or []
        scenario = tags[0] if tags else "unknown"
        per_scenario[scenario] = per_scenario.get(scenario, 0) + 1
        total += 1
    return QuotaReport(
        per_scenario=per_scenario,
        total=total,
        per_scenario_limit=per_scenario_limit,
        global_limit=global_limit,
    )
