"""Stripe payment gateway integration with dynamic database credentials and automated account upgrades."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional, Tuple

import requests
from sqlalchemy import select

from src.storage.db import get_session
from src.storage.models import PaymentGatewayConfig, PlatformPlan, SubscriptionPayment, User
from src.storage.plans import get_effective_daily_limit, get_plan_by_slug

logger = logging.getLogger(__name__)

STRIPE_API_BASE = "https://api.stripe.com/v1"


def get_stripe_config() -> Dict[str, Any]:
    """Retrieve Stripe configuration from database."""
    with get_session() as session:
        gw = session.scalar(select(PaymentGatewayConfig).where(PaymentGatewayConfig.gateway_key == "stripe"))
        if not gw or not gw.is_enabled:
            return {"enabled": False, "publishable_key": "", "secret_key": "", "webhook_secret": "", "mode": "test"}
        try:
            cfg = json.loads(gw.config_json or "{}")
        except Exception:
            cfg = {}
        return {
            "enabled": bool(gw.is_enabled),
            "publishable_key": (cfg.get("publishable_key") or "").strip(),
            "secret_key": (cfg.get("secret_key") or "").strip(),
            "webhook_secret": (cfg.get("webhook_secret") or "").strip(),
            "mode": cfg.get("mode", "test"),
        }


def test_stripe_credentials(secret_key: str) -> Tuple[bool, str]:
    """Test Stripe API connection using secret key."""
    if not secret_key:
        return False, "Secret key is empty"
    try:
        resp = requests.get(
            f"{STRIPE_API_BASE}/balance",
            headers={"Authorization": f"Bearer {secret_key}"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            currencies = [b.get("currency", "").upper() for b in data.get("available", [])]
            return True, f"Connected to Stripe successfully! Live currencies: {', '.join(currencies) or 'USD'}"
        else:
            err = resp.json().get("error", {}).get("message", resp.text)
            return False, f"Stripe error ({resp.status_code}): {err}"
    except Exception as exc:
        return False, f"Connection failed: {str(exc)}"


def create_stripe_checkout_session(
    user_id: int,
    user_email: str,
    plan_slug: str,
    success_url: str,
    cancel_url: str,
) -> Dict[str, Any]:
    """Create a Stripe Checkout Session for plan subscription."""
    cfg = get_stripe_config()
    if not cfg["enabled"]:
        raise ValueError("Stripe payments are currently disabled by administrator.")
    if not cfg["secret_key"]:
        raise ValueError("Stripe API credentials have not been configured.")

    secret_key = cfg["secret_key"]

    with get_session() as session:
        plan = get_plan_by_slug(session, plan_slug)
        if not plan:
            raise ValueError(f"Plan '{plan_slug}' not found.")

        # Unit amount in cents
        price_usd = float(plan.price_usd or 0.0)
        unit_amount = int(round(price_usd * 100))
        if unit_amount < 50:  # Stripe minimum is ~$0.50
            unit_amount = 50

        # Create session via Stripe REST API
        payload = {
            "payment_method_types[0]": "card",
            "mode": "payment",
            "customer_email": user_email,
            "line_items[0][price_data][currency]": "usd",
            "line_items[0][price_data][unit_amount]": str(unit_amount),
            "line_items[0][price_data][product_data][name]": f"AutoHunt {plan.name}",
            "line_items[0][price_data][product_data][description]": plan.description or f"AutoHunt Subscription: {plan.name}",
            "line_items[0][quantity]": "1",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "client_reference_id": f"u{user_id}_p{plan.slug}",
            "metadata[user_id]": str(user_id),
            "metadata[plan_slug]": plan.slug,
            "metadata[plan_name]": plan.name,
            "metadata[daily_limit]": str(plan.daily_apply_limit),
        }

        resp = requests.post(
            f"{STRIPE_API_BASE}/checkout/sessions",
            headers={"Authorization": f"Bearer {secret_key}"},
            data=payload,
            timeout=15,
        )

        if resp.status_code != 200:
            err = resp.json().get("error", {}).get("message", resp.text)
            logger.error("Stripe session creation failed: %s", err)
            raise ValueError(f"Stripe error: {err}")

        session_data = resp.json()
        session_id = session_data.get("id")
        session_url = session_data.get("url")

        # Record a pending SubscriptionPayment
        payment = SubscriptionPayment(
            user_id=user_id,
            plan_name=plan.slug,
            amount_usd=price_usd,
            amount_mad=plan.price_mad,
            currency="USD",
            payment_method="stripe",
            reference_code=session_id,
            receipt_note="Stripe Checkout Session Initiated",
            status="pending",
        )
        session.add(payment)
        session.commit()

        return {
            "ok": True,
            "session_id": session_id,
            "checkout_url": session_url,
            "publishable_key": cfg["publishable_key"],
        }


def process_stripe_session_completed(session_id: str) -> bool:
    """Verify and complete a Stripe Checkout Session, activating user subscription immediately."""
    cfg = get_stripe_config()
    secret_key = cfg["secret_key"]
    if not secret_key:
        return False

    try:
        resp = requests.get(
            f"{STRIPE_API_BASE}/checkout/sessions/{session_id}",
            headers={"Authorization": f"Bearer {secret_key}"},
            timeout=10,
        )
        if resp.status_code != 200:
            return False

        data = resp.json()
        if data.get("payment_status") != "paid":
            logger.info("Stripe session %s not paid yet: %s", session_id, data.get("payment_status"))
            return False

        meta = data.get("metadata", {})
        user_id_str = meta.get("user_id")
        plan_slug = meta.get("plan_slug", "pro")

        with get_session() as session:
            from datetime import datetime, timedelta, timezone
            payment = session.scalar(
                select(SubscriptionPayment).where(SubscriptionPayment.reference_code == session_id)
            )

            user_id = int(user_id_str) if user_id_str else (payment.user_id if payment else None)
            if not user_id:
                return False

            user = session.get(User, user_id)
            if not user:
                return False

            plan_limit = get_effective_daily_limit(session, plan_slug, fallback_limit=150)

            # Update User subscription
            user.current_plan = plan_slug
            user.daily_apply_limit = plan_limit
            user.plan_expires_at = datetime.now(timezone.utc) + timedelta(days=30)
            user.updated_at = datetime.now(timezone.utc)

            # Update or create payment record
            if payment:
                payment.status = "approved"
                payment.admin_notes = f"Automatically verified by Stripe Checkout ({session_id})"
                payment.reviewed_at = datetime.now(timezone.utc)
            else:
                amt_usd = float(data.get("amount_total", 0)) / 100.0
                payment = SubscriptionPayment(
                    user_id=user.id,
                    plan_name=plan_slug,
                    amount_usd=amt_usd,
                    amount_mad=int(amt_usd * 10),
                    currency="USD",
                    payment_method="stripe",
                    reference_code=session_id,
                    receipt_note=f"Stripe Payment Intent: {data.get('payment_intent')}",
                    status="approved",
                    admin_notes=f"Auto-verified via Stripe ({session_id})",
                    reviewed_at=datetime.now(timezone.utc),
                )
                session.add(payment)

            session.commit()
            logger.info("User #%s successfully auto-upgraded via Stripe to %s (%d apps/day)", user.id, plan_slug, plan_limit)
            return True
    except Exception as exc:
        logger.error("Failed to process Stripe session %s: %s", session_id, exc)
        return False
