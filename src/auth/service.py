"""Authentication, Session Management, Google/Gmail Verification, and RBAC Engine."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import urllib.request
from typing import Any, Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import select

from config.settings import settings
from src.storage.db import get_session
from src.storage.models import User, UserRole

logger = logging.getLogger(__name__)

# Secret key for HMAC token signing
_AUTH_SECRET = os.environ.get("AUTH_SECRET_KEY") or hashlib.sha256(
    (settings.dashboard_password + "_autohunt_secret_salt_2026").encode()
).hexdigest()

_TOKEN_TTL_SECONDS = 7 * 24 * 3600  # 7 days session lifetime
_http_basic = HTTPBasic(auto_error=False)


def hash_password(password: str) -> str:
    """Hash password using PBKDF2-HMAC-SHA256 with a unique random 16-byte salt."""
    salt = secrets.token_bytes(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return f"pbkdf2:sha256:{base64.b64encode(salt).decode('ascii')}:{base64.b64encode(key).decode('ascii')}"


def verify_password(password: str, hashed: str) -> bool:
    """Verify password against stored PBKDF2 hash using constant-time comparison."""
    if not hashed or not hashed.startswith("pbkdf2:sha256:"):
        return False
    try:
        parts = hashed.split(":")
        salt = base64.b64decode(parts[2].encode("ascii"))
        expected_key = base64.b64decode(parts[3].encode("ascii"))
        actual_key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
        return hmac.compare_digest(actual_key, expected_key)
    except Exception as exc:
        logger.debug("Password verification error: %s", exc)
        return False


def create_session_token(user_id: int, email: str, role: str) -> str:
    """Create a tamper-proof signed session token containing user payload and expiration."""
    payload = {
        "uid": user_id,
        "email": email,
        "role": role,
        "exp": int(time.time()) + _TOKEN_TTL_SECONDS,
        "rnd": secrets.token_hex(8),
    }
    payload_raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_raw).decode("ascii").rstrip("=")
    sig = hmac.new(_AUTH_SECRET.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")
    return f"{payload_b64}.{sig_b64}"


def decode_session_token(token: str) -> Optional[dict[str, Any]]:
    """Decode and cryptographically verify a session token. Returns payload dict or None if invalid/expired."""
    if not token or "." not in token:
        return None
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        expected_sig = hmac.new(_AUTH_SECRET.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).digest()
        actual_sig = base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))
        if not hmac.compare_digest(expected_sig, actual_sig):
            return None

        payload_raw = base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4))
        payload = json.loads(payload_raw.decode("utf-8"))
        if payload.get("exp", 0) < time.time():
            return None  # expired
        return payload
    except Exception as exc:
        logger.debug("Token decode error: %s", exc)
        return None


def verify_google_id_token(id_token: str) -> Optional[dict[str, Any]]:
    """Verify a Google OAuth2 / Google Identity Services ID token via Google's tokeninfo API.
    
    Verifies that the account is genuine, confirmed, and has an email.
    """
    if not id_token:
        return None
    try:
        url = f"https://oauth2.googleapis.com/tokeninfo?id_token={id_token}"
        req = urllib.request.Request(url, headers={"User-Agent": "AutoHunt-Auth/1.0"})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
            if data.get("error") or data.get("error_description"):
                logger.warning("Google tokeninfo returned error: %s", data.get("error_description"))
                return None
            
            # Ensure email is present and verified
            email_verified = data.get("email_verified")
            if email_verified is not True and email_verified != "true":
                logger.warning("Google account email is not verified: %s", data.get("email"))
                return None

            return {
                "sub": data.get("sub"),
                "email": data.get("email"),
                "name": data.get("name") or data.get("email", "").split("@")[0],
                "picture": data.get("picture"),
                "email_verified": True,
            }
    except Exception as exc:
        logger.warning("Failed to verify Google token with tokeninfo: %s", exc)
        return None


def get_current_user_optional(request: Request) -> Optional[User]:
    """Retrieve the currently authenticated user from session cookie, Bearer token, or Basic Auth fallback.
    
    Returns None if unauthenticated.
    """
    # 1. Check Authorization header: Bearer <token>
    auth_header = request.headers.get("Authorization", "")
    token = None
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()

    # 2. Check HTTP-only cookie
    if not token:
        token = request.cookies.get("session_token")

    if token:
        payload = decode_session_token(token)
        if payload and "uid" in payload:
            with get_session() as session:
                user = session.get(User, payload["uid"])
                if user and user.is_active:
                    session.expunge(user)
                    return user

    # 3. Fallback to Basic Auth for existing admin scripts / n8n / backwards compatibility
    if auth_header.startswith("Basic "):
        try:
            raw = base64.b64decode(auth_header[6:].strip()).decode("utf-8")
            u, p = raw.split(":", 1)
            # Accept dashboard username/password OR admin email/password from .env
            valid_basic = False
            if (
                settings.dashboard_username
                and settings.dashboard_password
                and secrets.compare_digest(u, settings.dashboard_username)
                and secrets.compare_digest(p, settings.dashboard_password)
            ):
                valid_basic = True
            elif (
                settings.admin_email
                and settings.admin_password
                and u.lower() == settings.admin_email.lower()
                and secrets.compare_digest(p, settings.admin_password)
            ):
                valid_basic = True

            if valid_basic:
                admin_email = (settings.admin_email or "").strip().lower()
                with get_session() as session:
                    # Try to find admin by email from .env first
                    admin_user = None
                    if admin_email:
                        admin_user = session.scalar(select(User).where(User.email == admin_email))
                    if not admin_user:
                        admin_user = session.scalar(select(User).where(User.role == UserRole.ADMIN.value))
                    if admin_user:
                        session.expunge(admin_user)
                        return admin_user
        except Exception:
            pass

    return None


def get_current_user(request: Request) -> User:
    """Dependency that enforces authentication. Raises HTTP 401 if unauthenticated."""
    user = get_current_user_optional(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please log in.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_admin(user_or_request: Any = Depends(get_current_user)) -> User:
    """Dependency or helper that enforces admin privileges. Raises HTTP 403 if user is not an admin."""
    req = None
    if isinstance(user_or_request, Request):
        req = user_or_request
        user = get_current_user(user_or_request)
    else:
        user = user_or_request

    if not user or user.role != UserRole.ADMIN.value:
        try:
            from src.notifications.publisher import publish_admin_alert
            path = req.url.path if req else "Admin Endpoint"
            client_ip = (req.client.host if req and req.client else "unknown")
            u_info = f"User #{user.id} ({user.email})" if user else "Unauthenticated user"
            publish_admin_alert(
                title="🚨 Security Alert: Unauthorized Admin Access Attempt",
                message=f"{u_info} attempted to access {path} from IP {client_ip}.",
                level="security",
                link="#admin",
                data={"path": path, "ip": client_ip, "user_id": user.id if user else None},
            )
        except Exception:
            pass

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: Administrator privileges required.",
        )
    return user

