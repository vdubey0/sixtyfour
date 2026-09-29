"""Server-owned limits. No global run deadline, including across clarification."""
from dataclasses import asdict, dataclass
from backend.blocks.sixtyfour import setting


def bounded_setting(name, default, ceiling):
    try:
        return min(ceiling, max(1, int(setting(name, str(default)))))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Limits:
    tool_attempts: int = 20
    model_requests: int = 30
    invalid_decisions: int = 2
    tool_failures: int = 2
    no_progress: int = 3
    model_timeout: int = 60
    max_rows: int = 5000

    @classmethod
    def configured(cls):
        # Configuration may lower the agreed ceilings, never disable them.
        return cls(**{name: bounded_setting('AGENT_' + name.upper(), value, value)
                      for name, value in asdict(cls()).items()})

    def public(self):
        return asdict(self)
