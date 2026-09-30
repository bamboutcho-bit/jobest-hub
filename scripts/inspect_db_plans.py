from src.storage.db import get_session
from src.storage.models import User, SubscriptionPayment
from sqlalchemy import select, text

with get_session() as session:
    users = session.scalars(select(User)).all()
    print(f"Total Users: {len(users)}")
    for u in users:
        print(f"  User #{u.id}: {u.email} | Role: {u.role} | Plan: {u.current_plan} | Limit: {u.daily_apply_limit}")
    
    payments = session.scalars(select(SubscriptionPayment)).all()
    print(f"Total Payments: {len(payments)}")
    for p in payments:
        print(f"  Payment #{p.id}: User {p.user_id} | Plan: {p.plan_name} | Status: {p.status} | Method: {p.payment_method}")
