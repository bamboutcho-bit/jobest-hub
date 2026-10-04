import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.storage.db import get_session
from src.storage.models import PlatformPlan, User
from sqlalchemy import select

with get_session() as s:
    plans = s.scalars(select(PlatformPlan).order_by(PlatformPlan.sort_order)).all()
    print("--- Current Database Plans ---")
    for p in plans:
        print(f"Plan #{p.id} [{p.slug}]: name='{p.name}' limit={p.daily_apply_limit} ai_calls={p.max_ai_calls_per_day} mad={p.price_mad} usd={p.price_usd} eur={p.price_eur}")

    print("\n--- Current Users ---")
    users = s.scalars(select(User).order_by(User.id)).all()
    for u in users:
        print(f"User #{u.id}: {u.email} | Role: {u.role} | Plan: {u.current_plan} | Limit: {u.daily_apply_limit}")
