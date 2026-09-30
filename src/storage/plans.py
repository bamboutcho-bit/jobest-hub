"""Dynamic platform plans, prices, quotas, and multi-gateway payment configurations."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import desc, select, text

from config.settings import settings
from src.storage.models import PaymentGatewayConfig, PlatformPlan, User

logger = logging.getLogger(__name__)

DEFAULT_PLANS: List[Dict[str, Any]] = [
    {
        "slug": "free",
        "name": "Free Tier",
        "badge": "Trial / Basic",
        "description": "Basic exploratory job search with essential alerts and limited automated applications.",
        "price_usd": 0.0,
        "price_mad": 0,
        "price_eur": 0.0,
        "price_usdt": 0.0,
        "billing_interval": "/ month",
        "daily_apply_limit": 5,
        "max_ai_calls_per_day": 10,
        "can_access_freelance": False,
        "is_active": True,
        "is_recommended": False,
        "features": [
            "5 Automated Applications / Day",
            "Basic Job Discovery & Deduplication",
            "Standard Resume Matching",
            "Email & Telegram Notifications",
        ],
        "sort_order": 0,
    },
    {
        "slug": "starter",
        "name": "Starter Hunter",
        "badge": "Entry / Graduate",
        "description": "Ideal for active job seekers targeting European, US, and Global Remote roles.",
        "price_usd": 15.0,
        "price_mad": 150,
        "price_eur": 14.0,
        "price_usdt": 15.0,
        "billing_interval": "/ month",
        "daily_apply_limit": 50,
        "max_ai_calls_per_day": 50,
        "can_access_freelance": False,
        "is_active": True,
        "is_recommended": False,
        "features": [
            "50 Automated Applications / Day",
            "Multi-Continent Discovery (Europe, US, Remote)",
            "0-2y Experience & Degree Matching",
            "Visa Sponsorship & Relocation Filter",
            "Live Sync & Telegram Alerts",
        ],
        "sort_order": 1,
    },
    {
        "slug": "pro",
        "name": "Pro Hunter & Freelancer",
        "badge": "Most Popular ⭐",
        "description": "Full-stack job hunt + automated freelance client deal acquisition.",
        "price_usd": 35.0,
        "price_mad": 350,
        "price_eur": 32.0,
        "price_usdt": 35.0,
        "billing_interval": "/ month",
        "daily_apply_limit": 150,
        "max_ai_calls_per_day": 200,
        "can_access_freelance": True,
        "is_active": True,
        "is_recommended": True,
        "features": [
            "150 Automated Applications / Day",
            "Automated Freelance Deal Finder (HN, Reddit, RemoteOK)",
            "AI Proposal & Pitch Generator + Smart Follow-ups",
            "Direct Recruiter & HR Contact Discovery",
            "Preserved Email Threading & Interview Auto-Reply",
            "Priority Match Scoring Engine",
        ],
        "sort_order": 2,
    },
    {
        "slug": "ultra",
        "name": "Executive & Agency",
        "badge": "Maximum Power 🚀",
        "description": "Unlimited scale, 24/7 background automation, and dedicated outreach.",
        "price_usd": 69.0,
        "price_mad": 690,
        "price_eur": 65.0,
        "price_usdt": 69.0,
        "billing_interval": "/ month",
        "daily_apply_limit": 9999,
        "max_ai_calls_per_day": 9999,
        "can_access_freelance": True,
        "is_active": True,
        "is_recommended": False,
        "features": [
            "Unlimited Applications / Day (9,999)",
            "24/7 Autonomous Daemon Engine",
            "Custom Domain Outreach & Multiple Mailboxes",
            "Unlimited Freelance Pitches & Deal Closing",
            "Dedicated 1-on-1 VIP Strategy Support",
        ],
        "sort_order": 3,
    },
]


def seed_default_plans_and_gateways(session) -> None:
    """Ensure platform_plans and payment_gateway_configs have initial seeds."""
    try:
        # 1. Seed Platform Plans
        existing_plans = {p.slug: p for p in session.scalars(select(PlatformPlan)).all()}
        for pdata in DEFAULT_PLANS:
            slug = pdata["slug"]
            if slug not in existing_plans:
                plan = PlatformPlan(
                    slug=slug,
                    name=pdata["name"],
                    badge=pdata.get("badge"),
                    description=pdata.get("description"),
                    price_usd=pdata["price_usd"],
                    price_mad=pdata["price_mad"],
                    price_eur=pdata["price_eur"],
                    price_usdt=pdata["price_usdt"],
                    billing_interval=pdata.get("billing_interval", "/ month"),
                    daily_apply_limit=pdata["daily_apply_limit"],
                    max_ai_calls_per_day=pdata.get("max_ai_calls_per_day", 50),
                    can_access_freelance=pdata.get("can_access_freelance", False),
                    is_active=pdata.get("is_active", True),
                    is_recommended=pdata.get("is_recommended", False),
                    features_json=json.dumps(pdata.get("features", [])),
                    sort_order=pdata.get("sort_order", 0),
                )
                session.add(plan)
        session.flush()

        # 2. Seed Payment Gateways
        existing_gateways = {g.gateway_key: g for g in session.scalars(select(PaymentGatewayConfig)).all()}

        default_gateways = [
            {
                "key": "stripe",
                "name": "Stripe (Credit / Debit Card & Apple Pay)",
                "category": "card_stripe",
                "is_enabled": False,  # Enabled once admin provides keys
                "config": {
                    "publishable_key": getattr(settings, "stripe_publishable_key", "") or "",
                    "secret_key": getattr(settings, "stripe_secret_key", "") or "",
                    "webhook_secret": getattr(settings, "stripe_webhook_secret", "") or "",
                    "mode": "test",  # test or live
                },
                "instructions": "Accept Visa, MasterCard, Amex, Apple Pay, and Google Pay worldwide via official Stripe Checkout with instant automated account activation.",
                "sort_order": 0,
            },
            {
                "key": "paypal",
                "name": "PayPal & Global Cards",
                "category": "paypal",
                "is_enabled": True,
                "config": {
                    "paypal_me_url": settings.payment_paypal_me_url or "https://paypal.me/AutoHuntAI",
                    "paypal_email": settings.payment_paypal_email or "hamzaoukhouyaxx@gmail.com",
                    "client_id": "",
                    "client_secret": "",
                    "mode": "sandbox",
                },
                "instructions": "Pay securely using your PayPal account balance or international Credit/Debit cards. No business registration needed.",
                "sort_order": 1,
            },
            {
                "key": "card_kofi",
                "name": "Ko-fi Direct Card & Apple Pay",
                "category": "card_kofi",
                "is_enabled": True,
                "config": {
                    "checkout_url": settings.payment_card_checkout_url or "https://ko-fi.com/autohunt",
                },
                "instructions": "One-click card checkout via Ko-fi platform. Fast, secure, and supports all major credit/debit cards.",
                "sort_order": 2,
            },
            {
                "key": "crypto_usdt_trc20",
                "name": "USDT (TRC-20)",
                "category": "crypto",
                "is_enabled": True,
                "config": {
                    "network": "Tron Network (TRC-20)",
                    "address": settings.payment_usdt_trc20_wallet or "TKr4pY4sVq4H2L5K8PqXjF6hS9wE3aM1bN",
                    "fee_note": "Lowest network transfer fees (~$1), instant blockchain confirmation",
                },
                "instructions": "Transfer USDT via Tron network (TRC-20) to the address below, then paste your TXID reference code.",
                "sort_order": 3,
            },
            {
                "key": "crypto_usdt_polygon",
                "name": "USDT / USDC (Polygon)",
                "category": "crypto",
                "is_enabled": True,
                "config": {
                    "network": "Polygon Network (POL / MATIC)",
                    "address": settings.payment_usdt_polygon_wallet or "0x71C8360662A3F36116316AC603e5491176f16183",
                    "fee_note": "Ultra-low gas fee (<$0.02), high speed",
                },
                "instructions": "Send USDT or USDC on Polygon Network. Fast confirmation and cent-level fees.",
                "sort_order": 4,
            },
            {
                "key": "crypto_solana",
                "name": "Solana (SOL / USDC)",
                "category": "crypto",
                "is_enabled": True,
                "config": {
                    "network": "Solana (SPL)",
                    "address": settings.payment_solana_wallet or "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU",
                    "fee_note": "Sub-second finality with negligible fees",
                },
                "instructions": "Send SOL or USDC (Solana SPL token) directly to the Solana address.",
                "sort_order": 5,
            },
            {
                "key": "crypto_btc",
                "name": "Bitcoin (BTC)",
                "category": "crypto",
                "is_enabled": True,
                "config": {
                    "network": "Bitcoin Mainnet",
                    "address": settings.payment_btc_wallet or "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh",
                    "fee_note": "Direct on-chain Bitcoin transfer",
                },
                "instructions": "Send BTC to the native SegWit address. Allow standard Bitcoin block confirmations.",
                "sort_order": 6,
            },
            {
                "key": "wise_revolut",
                "name": "Wise & Revolut (Bank Transfer / P2P)",
                "category": "p2p",
                "is_enabled": True,
                "config": {
                    "wise_email": settings.payment_wise_email or "hamzaoukhouyaxx@gmail.com",
                    "revolut_tag": settings.payment_revolut_tag or "@hamzaoukhouya",
                },
                "instructions": "Transfer USD, EUR, or GBP directly from Wise, Revolut, or SEPA/ACH with zero platform transaction fees.",
                "sort_order": 7,
            },
            {
                "key": "morocco_banks",
                "name": "Morocco Bank Wire & CashPlus (MAD)",
                "category": "bank_wire",
                "is_enabled": True,
                "config": {
                    "cih_rib": settings.payment_cih_rib or "230 780 0000000000000000 00",
                    "attijari_rib": settings.payment_attijari_rib or "007 780 0000000000000000 00",
                    "cashplus_info": settings.payment_cashplus_info or "Hamza Oukhouya (Casablanca, Morocco)",
                },
                "instructions": "Virement bancaire instantané au Maroc (CIH Bank, Attijariwafa Bank) ou versement physique en agence CashPlus / Wafacash.",
                "sort_order": 8,
            },
        ]

        for gw in default_gateways:
            k = gw["key"]
            if k not in existing_gateways:
                g_obj = PaymentGatewayConfig(
                    gateway_key=k,
                    name=gw["name"],
                    category=gw["category"],
                    is_enabled=gw["is_enabled"],
                    config_json=json.dumps(gw["config"]),
                    instructions=gw["instructions"],
                    sort_order=gw["sort_order"],
                )
                session.add(g_obj)

        session.commit()
    except Exception as exc:
        session.rollback()
        logger.warning("Failed to seed default plans and gateways: %s", exc)


def resolve_plan_slug(slug: str) -> str:
    """Normalize legacy or alias plan slugs."""
    s = (slug or "free").strip().lower()
    if s in ("starter_99", "starter"):
        return "starter"
    if s in ("pro_499", "pro"):
        return "pro"
    return s


def get_all_plans_db(session, include_inactive: bool = False) -> List[PlatformPlan]:
    """Retrieve all platform plans ordered by sort_order."""
    seed_default_plans_and_gateways(session)
    stmt = select(PlatformPlan)
    if not include_inactive:
        stmt = stmt.where(PlatformPlan.is_active == True)
    stmt = stmt.order_by(PlatformPlan.sort_order.asc(), PlatformPlan.id.asc())
    return list(session.scalars(stmt).all())


def get_plan_by_slug(session, slug: str) -> Optional[PlatformPlan]:
    """Find a plan by slug with alias fallback."""
    seed_default_plans_and_gateways(session)
    clean_slug = (slug or "").strip().lower()
    plan = session.scalar(select(PlatformPlan).where(PlatformPlan.slug == clean_slug))
    if not plan:
        normalized = resolve_plan_slug(clean_slug)
        if normalized != clean_slug:
            plan = session.scalar(select(PlatformPlan).where(PlatformPlan.slug == normalized))
    return plan


def get_effective_daily_limit(session, plan_slug: str, fallback_limit: Optional[int] = None) -> int:
    """Dynamically get the daily application quota configured for a plan."""
    plan = get_plan_by_slug(session, plan_slug)
    if plan and plan.daily_apply_limit is not None:
        return plan.daily_apply_limit
    if fallback_limit is not None:
        return fallback_limit
    # Fallback safety table
    safety = {
        "free": 5,
        "starter": 50,
        "starter_99": 50,
        "pro": 150,
        "pro_499": 150,
        "ultra": 9999,
        "unlimited": 9999,
    }
    return safety.get(resolve_plan_slug(plan_slug), 5)


def get_payment_gateways_dict(session, include_secrets: bool = False) -> Dict[str, Any]:
    """Format active payment gateways for checkout modal and API."""
    seed_default_plans_and_gateways(session)
    stmt = select(PaymentGatewayConfig).order_by(PaymentGatewayConfig.sort_order.asc())
    if not include_secrets:
        stmt = stmt.where(PaymentGatewayConfig.is_enabled == True)
    gateways = list(session.scalars(stmt).all())

    result: Dict[str, Any] = {}
    for g in gateways:
        try:
            cfg = json.loads(g.config_json or "{}")
        except Exception:
            cfg = {}

        if not include_secrets:
            # Mask or strip sensitive secret keys from public client response
            if "secret_key" in cfg:
                cfg.pop("secret_key", None)
            if "webhook_secret" in cfg:
                cfg.pop("webhook_secret", None)
            if "client_secret" in cfg:
                cfg.pop("client_secret", None)

        result[g.gateway_key] = {
            "name": g.name,
            "category": g.category,
            "is_enabled": g.is_enabled,
            "instructions": g.instructions,
            "sort_order": g.sort_order,
            **cfg,
        }

    return result


def sync_plan_quota_to_users(session, plan_slug: str, new_daily_limit: int) -> int:
    """Propagate updated plan quotas immediately to all registered users on that plan."""
    norm_slug = resolve_plan_slug(plan_slug)
    # Target exact slug, normalized slug, and common aliases
    matching_slugs = {plan_slug.strip().lower(), norm_slug}
    if norm_slug == "starter":
        matching_slugs.add("starter_99")
    elif norm_slug == "pro":
        matching_slugs.add("pro_499")

    stmt = select(User).where(User.current_plan.in_(list(matching_slugs)))
    users = list(session.scalars(stmt).all())
    count = 0
    for u in users:
        u.daily_apply_limit = new_daily_limit
        u.updated_at = datetime.now(timezone.utc)
        count += 1
    session.commit()
    logger.info("Synchronized %d users on plan '%s' to new daily limit %d", count, plan_slug, new_daily_limit)
    return count


def sync_user_quota_to_plan(session, user: User) -> int:
    """Synchronize a single user's daily limit to their active plan's defined quota."""
    plan_slug = (user.current_plan or "free").strip().lower()
    target_limit = get_effective_daily_limit(session, plan_slug, fallback_limit=5)
    user.daily_apply_limit = target_limit
    user.updated_at = datetime.now(timezone.utc)
    session.commit()
    logger.info("Synchronized user #%s (%s) quota to %d apps/day (Plan: %s)", user.id, user.email, target_limit, user.current_plan)
    return target_limit


def sync_all_users_quotas(session) -> int:
    """Synchronize all registered users' daily limits to their assigned platform plan."""
    users = list(session.scalars(select(User)).all())
    plans = get_all_plans_db(session, include_inactive=True)
    lookup: dict[str, int] = {}
    for p in plans:
        lookup[p.slug.strip().lower()] = p.daily_apply_limit
        norm = resolve_plan_slug(p.slug)
        if norm not in lookup:
            lookup[norm] = p.daily_apply_limit

    if "starter" in lookup and "starter_99" not in lookup:
        lookup["starter_99"] = lookup["starter"]
    if "pro" in lookup and "pro_499" not in lookup:
        lookup["pro_499"] = lookup["pro"]

    count = 0
    for u in users:
        slug = (u.current_plan or "free").strip().lower()
        target_limit = lookup.get(slug, lookup.get(resolve_plan_slug(slug), 5))
        if u.daily_apply_limit != target_limit:
            u.daily_apply_limit = target_limit
            u.updated_at = datetime.now(timezone.utc)
            count += 1

    session.commit()
    logger.info("Synchronized all users to plan quotas (%d users updated)", count)
    return count

