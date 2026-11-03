"""Extraction budgets are independent of the other roles' budgets."""
from dataclasses import dataclass

from console.roles.common import Config as RoleConfig


@dataclass(frozen=True)
class Config(RoleConfig):
    per_minute: int = 72
    per_hour: int = 72
    scan_limit: int = 20
    ai_timeout: float = 30
    scan_ai_limit: int = 8
    scan_timeout: float = 180

    def __post_init__(self):
        super().__post_init__()
        if type(self.scan_ai_limit) is not int or not 1 <= self.scan_ai_limit <= 100:
            raise ValueError('单次 AI 会话数量须为 1 至 100 的整数')

    @classmethod
    def coerce(cls, config):
        if config is None or isinstance(config, cls):
            return config or cls()
        # Existing CLI/HTTP builders may supply a shared roles.Config. Carry
        # over scan/date settings, never that shared quota or its 365s timeout.
        values = dict(timezone=config.timezone, scan_limit=config.scan_limit)
        if config.ai_timeout != RoleConfig.ai_timeout:
            values['ai_timeout'] = config.ai_timeout
        return cls(**values)
