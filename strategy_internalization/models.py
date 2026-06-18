from dataclasses import dataclass, field
from typing import Optional

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

@dataclass
class TaskSignals:
    scenario: Optional[str]
    keywords: list[str]
    text: str

@dataclass
class StrategyPacket:
    cards: list[StrategyCard]
    text: str
    tokens: int
    degraded: bool
    retrieved: bool
    top_scores: list[float]

    @property
    def cards_ids(self) -> list[str]:
        """便捷访问：注入卡的 id 列表（P1 测试用）。"""
        return [c.id for c in self.cards]
