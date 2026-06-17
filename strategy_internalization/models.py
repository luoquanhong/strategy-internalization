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
