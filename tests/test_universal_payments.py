"""Unit & Integration Tests for Universal Multi-Currency Payment System (No-LTD Required)."""
import base64
import pytest
from fastapi.testclient import TestClient

from src.dashboard.app import app
from src.storage.db import get_session
from src.storage.models import User, UserRole, SubscriptionPayment
from src.auth.service import create_session_token


@pytest.fixture
def client():
    return TestClient(app)


def test_payment_methods_endpoint(client):
    """Verify payment methods endpoint returns plans and non-LTD channels."""
    res = client.get("/api/payments/methods")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    
    # Plans verification
    slugs = [p["slug"] for p in data["plans"]]
    assert "starter" in slugs
    assert "pro" in slugs
    assert "ultra" in slugs

    # Channels verification (no corporate Ltd required)
    methods = data["methods"]
    assert "usdt_trc20" in methods
    assert "usdt_polygon" in methods
    assert "solana" in methods
    assert "btc" in methods
    assert "paypal" in methods
    assert "card_kofi" in methods
    assert "wise_revolut" in methods
    assert "morocco" in methods


def test_checkout_and_receipt_lifecycle(client):
    """Test full cycle: checkout with receipt, view receipt, and admin approval."""
    with get_session() as session:
        user = session.query(User).filter_by(role=UserRole.ADMIN.value).first()
        if not user:
            user = session.query(User).first()
        user_id = user.id
        user_email = user.email
        user_role = user.role

    token = create_session_token(user_id, user_email, user_role)
    client.cookies.set("session_token", token)

    mock_img = b"TEST_PAYMENT_RECEIPT_IMAGE_CONTENT"
    b64_data = "data:image/png;base64," + base64.b64encode(mock_img).decode()

    payload = {
        "plan_name": "pro",
        "amount": 35.0,
        "amount_usd": 35.0,
        "currency": "USD",
        "payment_method": "usdt_trc20",
        "reference_code": "0xABCDEF1234567890",
        "receipt_note": "USDT payment proof test",
        "receipt_image_base64": b64_data
    }

    # 1. Checkout
    res = client.post("/api/payments/checkout", json=payload)
    assert res.status_code == 200
    pay_id = res.json()["payment_id"]

    # 2. My payments
    res = client.get("/api/payments/my-payments")
    assert res.status_code == 200
    my_pays = res.json()["payments"]
    target = next((p for p in my_pays if p["id"] == pay_id), None)
    assert target is not None
    assert target["has_receipt"] is True

    # 3. Receipt fetch
    res = client.get(target["receipt_url"])
    assert res.status_code == 200
    assert res.content == mock_img

    # 4. Admin approve
    res = client.post(f"/api/admin/payments/{pay_id}/approve", json={"admin_notes": "Verified on blockchain"})
    assert res.status_code == 200

    # 5. Verify user upgraded
    with get_session() as session:
        u = session.get(User, user_id)
        assert u.current_plan == "pro"
        assert u.daily_apply_limit == 150
