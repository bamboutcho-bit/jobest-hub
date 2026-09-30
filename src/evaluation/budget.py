"""Backward-compatible run budget facade.

The daily portion is persisted/enforced by src.storage.quota. The per-run
portion remains an in-process guard for the pipeline's single run.
"""
from src.storage.quota import get_daily_quota, reserve_ai_call
from config.settings import settings


class ClaudeBudget:
    def __init__(self, session):
        self.session = session
        self.calls = 0

    @property
    def day_calls(self) -> int:
        return get_daily_quota(self.session).ai_calls

    def can_call(self) -> bool:
        return (
            self.calls < max(0, settings.max_claude_calls_per_run)
            and self.day_calls < max(0, settings.max_ai_calls_per_day)
        )

    def consume(self) -> None:
        if not self.can_call():
            raise RuntimeError("AI evaluation budget exhausted")
        reserve_ai_call(self.session)
        self.calls += 1
