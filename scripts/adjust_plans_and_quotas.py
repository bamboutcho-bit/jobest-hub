import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.storage.db import get_session
from src.storage.models import PlatformPlan, User
from sqlalchemy import select

PLAN_UPDATES = {
    "free": {
        "daily_apply_limit": 5,
        "max_ai_calls_per_day": 10,
        "features": [
            "5 Automated Applications / Day",
            "Basic Job Discovery & Deduplication",
            "Standard Resume Matching",
            "Email & Telegram Notifications",
        ],
    },
    "starter": {
        "daily_apply_limit": 25,
        "max_ai_calls_per_day": 50,
        "price_usd": 15.0,
        "price_mad": 150,
        "price_eur": 14.0,
        "description": "Ideal for active job seekers targeting European, US, and Global Remote roles.",
        "features": [
            "25 Automated Applications / Day (100% Gmail Safe)",
            "Multi-Continent Discovery (Europe, US, Remote)",
            "0-2y Experience & Degree Matching",
            "Visa Sponsorship & Relocation Filter",
            "Live Sync & Telegram Alerts",
        ],
    },
    "pro": {
        "daily_apply_limit": 50,
        "max_ai_calls_per_day": 100,
        "price_usd": 35.0,
        "price_mad": 350,
        "price_eur": 32.0,
        "description": "Full-stack job hunt + automated freelance client deal acquisition.",
        "features": [
            "50 Automated Applications / Day (High Deliverability)",
            "Automated Freelance Deal Finder (HN, Reddit, RemoteOK)",
            "AI Proposal & Pitch Generator + Smart Follow-ups",
            "Direct Recruiter & HR Contact Discovery",
            "Preserved Email Threading & Auto-Reply",
            "Priority Match Scoring Engine",
        ],
    },
    "ultra": {
        "daily_apply_limit": 100,
        "max_ai_calls_per_day": 250,
        "price_usd": 69.0,
        "price_mad": 690,
        "price_eur": 65.0,
        "description": "Maximum safe volume, 24/7 background automation, and dedicated outreach.",
        "features": [
            "100 Automated Applications / Day (Maximum Safe Volume)",
            "24/7 Autonomous Daemon Engine",
            "Custom Domain Outreach & Multiple Mailboxes",
            "Unlimited Freelance Pitches & Deal Closing",
            "Dedicated 1-on-1 VIP Strategy Support",
        ],
    },
}

with get_session() as session:
    print("=== Updating Platform Plans in Database ===")
    plans = session.scalars(select(PlatformPlan)).all()
    for p in plans:
        up = PLAN_UPDATES.get(p.slug)
        if up:
            p.daily_apply_limit = up["daily_apply_limit"]
            p.max_ai_calls_per_day = up["max_ai_calls_per_day"]
            if "price_usd" in up:
                p.price_usd = up["price_usd"]
            if "price_mad" in up:
                p.price_mad = up["price_mad"]
            if "price_eur" in up:
                p.price_eur = up["price_eur"]
            if "description" in up:
                p.description = up["description"]
            p.features_json = json.dumps(up["features"])
            print(f"Updated Plan #{p.id} [{p.slug}]: Limit={p.daily_apply_limit}, MAD={p.price_mad}, USD={p.price_usd}")

    print("\n=== Updating Users Quotas in Database ===")
    users = session.scalars(select(User)).all()
    for u in users:
        old_limit = u.daily_apply_limit
        if u.role == "admin":
            # Set safe admin default to 100 (high volume, safe from Gmail blocks)
            # Admin can change it anytime in the dashboard
            u.daily_apply_limit = 100
        elif u.current_plan and u.current_plan in PLAN_UPDATES:
            u.daily_apply_limit = PLAN_UPDATES[u.current_plan]["daily_apply_limit"]
        else:
            u.daily_apply_limit = 25
        print(f"User #{u.id} ({u.email}, role={u.role}, plan={u.current_plan}): limit {old_limit} -> {u.daily_apply_limit}")

    session.commit()
    print("\nAll database plans and user quotas adjusted successfully!")
