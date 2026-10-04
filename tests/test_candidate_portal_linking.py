"""Tests for candidate LinkedIn and Indeed portal linking, verification, and auto-apply routing."""
import json
from unittest.mock import MagicMock
import pytest

from config.settings import settings
from src.application.auto_apply import _candidate_application_url
from src.auth.service import create_session_token
from src.dashboard.app import (
    PortalConnectRequest,
    PortalVerifyRequest,
    api_user_connect_indeed,
    api_user_connect_linkedin,
    api_user_disconnect_indeed,
    api_user_disconnect_linkedin,
    api_user_get_integrations,
    api_user_verify_integration,
)
from src.storage.db import get_session, init_db
from src.storage.models import User, UserRole, UserSetting
from src.storage.user_settings import get_user_effective_settings


@pytest.fixture(autouse=True)
def setup_db():
    init_db()


@pytest.fixture
def mock_user_request():
    with get_session() as session:
        user = session.query(User).filter_by(email="portal_test_candidate@example.com").first()
        if not user:
            user = User(
                email="portal_test_candidate@example.com",
                password_hash="testpasshash",
                full_name="Portal Candidate",
                role=UserRole.USER.value,
                current_plan="pro",
                is_active=True,
            )
            session.add(user)
            session.commit()
            session.refresh(user)

        user_id = user.id
        user_email = user.email
        user_role = user.role

    token = create_session_token(user_id, user_email, user_role)
    req = MagicMock()
    req.headers = {"Authorization": f"Bearer {token}"}
    req.cookies = {"session_token": token}
    return req, user_id


def test_portal_integrations_api_lifecycle(mock_user_request, monkeypatch):
    req, user_id = mock_user_request

    # 1. Initial status: should return dictionary with linkedin and indeed info
    status_res = api_user_get_integrations(req)
    assert status_res["ok"] is True
    assert "linkedin" in status_res
    assert "indeed" in status_res

    # 2. Connect LinkedIn account
    fake_li_at = "AQEDASampleValidLinkedInSessionCookieToken1234567890"
    conn_req = PortalConnectRequest(
        cookie=f"li_at={fake_li_at}; Path=/; Domain=.linkedin.com",
        profile_url="https://www.linkedin.com/in/testcandidate",
        auto_apply=True,
    )
    conn_res = api_user_connect_linkedin(conn_req, req)
    assert conn_res["ok"] is True
    assert "connected" in conn_res["message"].lower()

    # Check status now reflects connection
    status_res2 = api_user_get_integrations(req)
    assert status_res2["linkedin"]["connected"] is True
    assert status_res2["linkedin"]["auto_apply_enabled"] is True
    assert status_res2["linkedin"]["verified_at"] is not None

    # Verify user effective settings
    eff = get_user_effective_settings(user_id=user_id)
    assert eff.get("linkedin_cookie") == fake_li_at
    assert eff.get("auto_apply_linkedin_enabled") is True

    # 3. Connect Indeed account
    fake_ind_cookie = "SHARED_SESSION_ID=abc123indeedtoken456; CTK=123"
    ind_conn_req = PortalConnectRequest(
        cookie=fake_ind_cookie,
        auto_apply=True,
    )
    ind_res = api_user_connect_indeed(ind_conn_req, req)
    assert ind_res["ok"] is True

    # Check Indeed is connected
    status_res3 = api_user_get_integrations(req)
    assert status_res3["indeed"]["connected"] is True
    assert status_res3["indeed"]["auto_apply_enabled"] is True

    # 4. Test Verification endpoint for Indeed
    verify_ind = api_user_verify_integration(
        PortalVerifyRequest(platform="indeed"),
        req,
    )
    assert verify_ind["ok"] is True
    assert verify_ind["connected"] is True

    # 5. Disconnect LinkedIn
    disc_li = api_user_disconnect_linkedin(req)
    assert disc_li["ok"] is True

    status_after_disc = api_user_get_integrations(req)
    assert status_after_disc["linkedin"]["connected"] is False

    # 6. Disconnect Indeed
    disc_ind = api_user_disconnect_indeed(req)
    assert disc_ind["ok"] is True

    status_final = api_user_get_integrations(req)
    assert status_final["indeed"]["connected"] is False


def test_candidate_application_url_user_override(mock_user_request, monkeypatch):
    req, user_id = mock_user_request

    # Connect LinkedIn with auto_apply = True
    conn_req = PortalConnectRequest(
        cookie="AQEDASampleValidLinkedInSessionCookieToken1234567890",
        auto_apply=True,
    )
    api_user_connect_linkedin(conn_req, req)

    li_url = "https://www.linkedin.com/jobs/view/1234567890"

    # Enabled for user: url is returned
    app_url = _candidate_application_url(
        raw_description="Check out this role",
        direct_url=None,
        source_url=li_url,
        user_id=user_id,
    )
    assert app_url == li_url

    # Now simulate user turning off LinkedIn auto-apply
    with get_session() as session:
        us = session.query(UserSetting).filter_by(user_id=user_id).first()
        custom = json.loads(us.custom_env_json or "{}")
        custom["AUTO_APPLY_LINKEDIN_ENABLED"] = False
        us.custom_env_json = json.dumps(custom)
        session.commit()

    # Even if global setting is True, user's preference disables it
    monkeypatch.setattr(settings, "auto_apply_linkedin_enabled", True)
    app_url_disabled = _candidate_application_url(
        raw_description="Check out this role",
        direct_url=None,
        source_url=li_url,
        user_id=user_id,
    )
    assert app_url_disabled is None
