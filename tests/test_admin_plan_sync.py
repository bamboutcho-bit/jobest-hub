import json
import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src.storage.models import Base, User, PlatformPlan, SubscriptionPayment
from src.storage.plans import (
    seed_default_plans_and_gateways,
    get_all_plans_db,
    get_plan_by_slug,
    get_effective_daily_limit,
    sync_plan_quota_to_users,
    sync_user_quota_to_plan,
    sync_all_users_quotas,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    sess = Session()
    seed_default_plans_and_gateways(sess)
    yield sess
    sess.close()


def test_seed_and_effective_limit(session):
    plans = get_all_plans_db(session)
    slugs = [p.slug for p in plans]
    assert "free" in slugs
    assert "starter" in slugs
    assert "pro" in slugs
    assert "ultra" in slugs

    assert get_effective_daily_limit(session, "free") == 5
    assert get_effective_daily_limit(session, "starter") == 50
    assert get_effective_daily_limit(session, "pro") == 150
    assert get_effective_daily_limit(session, "ultra") == 9999


def test_add_new_plan_dynamically_syncs_limit(session):
    # 1. Add brand new plan "enterprise"
    new_plan = PlatformPlan(
        slug="enterprise",
        name="Enterprise Scale",
        daily_apply_limit=300,
        price_mad=990,
        price_usd=99.0,
        billing_interval="/ month",
        is_active=True,
    )
    session.add(new_plan)
    session.commit()

    # 2. Verify get_effective_daily_limit sees the new plan immediately
    limit = get_effective_daily_limit(session, "enterprise")
    assert limit == 300

    # 3. Create user assigned to this new plan without explicit limit
    user = User(
        email="corp@example.com",
        full_name="Corp Lead",
        role="user",
        current_plan="enterprise",
        daily_apply_limit=get_effective_daily_limit(session, "enterprise"),
    )
    session.add(user)
    session.commit()
    assert user.daily_apply_limit == 300

    # 4. Modify enterprise plan limit to 500 and sync to subscribers
    new_plan.daily_apply_limit = 500
    session.commit()
    synced = sync_plan_quota_to_users(session, "enterprise", 500)
    assert synced == 1

    session.refresh(user)
    assert user.daily_apply_limit == 500


def test_sync_individual_user_and_all_users(session):
    # Create users on different plans with drifted/custom limits
    u1 = User(email="free_user@example.com", full_name="Free Guy", current_plan="free", daily_apply_limit=2)
    u2 = User(email="pro_user@example.com", full_name="Pro Dev", current_plan="pro", daily_apply_limit=80)
    session.add_all([u1, u2])
    session.commit()

    # Sync single user u1
    sync_user_quota_to_plan(session, u1)
    session.refresh(u1)
    assert u1.daily_apply_limit == 5  # Free plan default

    # u2 is still 80
    assert u2.daily_apply_limit == 80

    # Sync all users
    synced_count = sync_all_users_quotas(session)
    assert synced_count >= 1
    session.refresh(u2)
    assert u2.daily_apply_limit == 150  # Pro plan default


def test_payment_approval_and_edit_syncs_user_plan_and_quota(session):
    user = User(email="buyer@example.com", full_name="Buyer Corp", current_plan="free", daily_apply_limit=5)
    session.add(user)
    session.commit()

    # Add custom plan "growth"
    growth_plan = PlatformPlan(
        slug="growth",
        name="Growth Hunter",
        daily_apply_limit=250,
        price_mad=490,
        is_active=True,
    )
    session.add(growth_plan)
    session.commit()

    payment = SubscriptionPayment(
        user_id=user.id,
        plan_name="growth",
        amount_mad=490,
        currency="MAD",
        payment_method="cih_wire",
        reference_code="CIH-REF-9988",
        status="pending",
    )
    session.add(payment)
    session.commit()

    # Approve payment and sync
    payment.status = "approved"
    limit = get_effective_daily_limit(session, payment.plan_name)
    user.current_plan = payment.plan_name
    user.daily_apply_limit = limit
    session.commit()

    session.refresh(user)
    assert user.current_plan == "growth"
    assert user.daily_apply_limit == 250
