"""User environment settings resolver with system fallback."""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from sqlalchemy import select

from config.settings import settings
from src.storage.db import get_session
from src.storage.models import UserSetting

logger = logging.getLogger(__name__)


def get_user_effective_settings(user_id: Optional[int] = None) -> dict[str, Any]:
    """Retrieve effective environment settings for a given user.
    
    If the user has customized their settings in the platform, those values
    are used. Any unconfigured fields fall back to the system defaults in .env.
    """
    effective: dict[str, Any] = {
        # Outbound / Sender
        "sender_email": settings.sender_email,
        "sender_name": settings.sender_display_name,
        "smtp_host": settings.sender_smtp_host,
        "smtp_port": settings.sender_smtp_port,
        "smtp_password": settings.sender_smtp_password,
        "imap_host": settings.imap_host,
        "imap_port": settings.imap_port,
        "imap_password": settings.imap_password,

        # Candidate links
        "phone_number": settings.candidate_phone,
        "linkedin_url": settings.candidate_linkedin_url,
        "github_url": settings.candidate_github_url,
        "portfolio_url": settings.candidate_portfolio_url,

        # Matching & auto-apply
        "min_match_score": settings.min_match_score,
        "auto_apply_mode": settings.auto_apply_mode,
        "auto_apply_min_score": settings.auto_apply_min_score,

        # Alerts
        "telegram_bot_token": settings.telegram_bot_token,
        "telegram_chat_id": settings.telegram_chat_id,
        "alert_email": settings.alert_email_to,

        # Portals & Cookies
        "auto_apply_linkedin_enabled": settings.auto_apply_linkedin_enabled,
        "auto_apply_indeed_enabled": settings.auto_apply_indeed_enabled,
        "linkedin_cookie": settings.linkedin_cookie,
        "indeed_cookie": settings.indeed_cookie,

        # Custom env overrides
        "custom_env": {},
    }

    try:
        with get_session() as session:
            if user_id:
                user_setting = session.scalar(select(UserSetting).where(UserSetting.user_id == user_id))
            else:
                user_setting = session.scalar(select(UserSetting).order_by(UserSetting.updated_at.desc()).limit(1))
            if user_setting:
                if user_setting.sender_email and (user_setting.smtp_password or user_setting.sender_email == settings.sender_email):
                    effective["sender_email"] = user_setting.sender_email
                if user_setting.sender_name:
                    effective["sender_name"] = user_setting.sender_name
                if user_setting.smtp_host and user_setting.smtp_password:
                    effective["smtp_host"] = user_setting.smtp_host
                if user_setting.smtp_port and user_setting.smtp_password:
                    effective["smtp_port"] = user_setting.smtp_port
                if user_setting.smtp_password:
                    effective["smtp_password"] = user_setting.smtp_password
                if user_setting.imap_host:
                    effective["imap_host"] = user_setting.imap_host
                if user_setting.imap_port:
                    effective["imap_port"] = user_setting.imap_port
                if user_setting.imap_password:
                    effective["imap_password"] = user_setting.imap_password

                if user_setting.phone_number:
                    effective["phone_number"] = user_setting.phone_number
                if user_setting.linkedin_url:
                    effective["linkedin_url"] = user_setting.linkedin_url
                if user_setting.github_url:
                    effective["github_url"] = user_setting.github_url
                if user_setting.portfolio_url:
                    effective["portfolio_url"] = user_setting.portfolio_url

                if user_setting.min_match_score is not None:
                    effective["min_match_score"] = user_setting.min_match_score
                if user_setting.auto_apply_mode:
                    effective["auto_apply_mode"] = user_setting.auto_apply_mode
                if user_setting.auto_apply_min_score is not None:
                    effective["auto_apply_min_score"] = user_setting.auto_apply_min_score

                if user_setting.telegram_bot_token:
                    effective["telegram_bot_token"] = user_setting.telegram_bot_token
                if user_setting.telegram_chat_id:
                    effective["telegram_chat_id"] = user_setting.telegram_chat_id
                if user_setting.alert_email:
                    effective["alert_email"] = user_setting.alert_email

                if user_setting.custom_env_json:
                    try:
                        custom = json.loads(user_setting.custom_env_json)
                        if isinstance(custom, dict):
                            effective["custom_env"] = custom
                            # Also map top-level keys if overridden
                            for k, v in custom.items():
                                effective[k.lower()] = v
                    except Exception:
                        pass
    except Exception as exc:
        logger.warning("Failed to fetch custom settings for user %s: %s", user_id, exc)

    return effective
