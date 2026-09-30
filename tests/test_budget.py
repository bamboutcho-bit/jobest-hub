from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.evaluation.budget import ClaudeBudget
from src.storage.models import Base


def test_budget_enforces_run_cap(monkeypatch):
    monkeypatch.setattr("config.settings.settings.max_claude_calls_per_run", 2)
    monkeypatch.setattr("config.settings.settings.max_claude_calls_per_day", 99)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    budget = ClaudeBudget(session)
    assert budget.can_call()
    budget.consume()
    budget.consume()
    assert budget.calls == 2
    assert not budget.can_call()
