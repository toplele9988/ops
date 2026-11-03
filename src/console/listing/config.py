"""Listing uses its own small quota within the existing role reservation table."""
from dataclasses import dataclass

from console.roles.common import Config as RoleConfig


@dataclass(frozen=True)
class Config(RoleConfig):
    # One service request may use up to three official_runner attempts.
    # Reserve all three conservatively, allowing at most 12 listing requests/hour.
    per_minute: int = 36
    per_hour: int = 36
    ai_timeout: float = 120
    knowledge_limit: int = 8
    default_target_margin_bp: int = 1500

    def __post_init__(self):
        super().__post_init__()
        if type(self.knowledge_limit) is not int or not 1 <= self.knowledge_limit <= 20:
            raise ValueError('发品知识片段数量须为 1 至 20')
        if type(self.default_target_margin_bp) is not int or not 0 <= self.default_target_margin_bp <= 9000:
            raise ValueError('默认目标利润率无效')

