"""Integration tests for multilingual chatbot and admin live support."""
import pytest
from fastapi.testclient import TestClient

from src.dashboard.app import app
from src.auth.service import create_session_token, hash_password
from src.storage.db import get_session
from src.storage.models import User, SupportMessage, Notification


@pytest.fixture
def client():
    return TestClient(app)


def test_chatbot_and_admin_support_flow(client):
    # Setup test users
    cand_id = None
    admin_id = None
    with get_session() as session:
        admin = session.query(User).filter(User.role == "admin").first()
        if not admin:
            admin = User(
                email="admin_chat_fixture@autohunt.internal",
                full_name="Admin Fixture",
                password_hash=hash_password("adminpass"),
                role="admin",
                is_active=True,
                is_verified=True,
            )
            session.add(admin)
            session.commit()
            session.refresh(admin)
        admin_id = admin.id

        cand = session.query(User).filter(User.email == "amina_chat_test@example.com").first()
        if not cand:
            cand = User(
                email="amina_chat_test@example.com",
                full_name="Amina El Mansouri",
                password_hash=hash_password("candpass123"),
                role="user",
                current_plan="starter_99",
                daily_apply_limit=50,
                is_active=True,
                is_verified=True,
            )
            session.add(cand)
            session.commit()
            session.refresh(cand)
        cand_id = cand.id

    cand_token = create_session_token(cand_id, "amina_chat_test@example.com", "user")
    admin_token = create_session_token(admin_id, admin.email, "admin")

    cand_headers = {"Authorization": f"Bearer {cand_token}"}
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    try:
        # 1. Candidate sends French question to chatbot
        r1 = client.post("/api/chat/send", json={"message": "Comment fonctionne la candidature automatique ?"}, headers=cand_headers)
        assert r1.status_code == 200
        d1 = r1.json()
        assert d1["ok"] is True
        assert d1["user_message"]["message"] == "Comment fonctionne la candidature automatique ?"
        assert d1["bot_message"]["sender_role"] == "bot"
        assert d1["needs_admin"] is False
        assert "candidature" in d1["bot_message"]["message"].lower() or "auto" in d1["bot_message"]["message"].lower()

        # 2. Candidate fetches history
        r2 = client.get("/api/chat/history", headers=cand_headers)
        assert r2.status_code == 200
        history = r2.json()["messages"]
        assert len(history) >= 2

        # 3. Candidate asks for human admin
        r3 = client.post("/api/chat/send", json={"message": "Je voudrais parler à un administrateur s'il vous plaît"}, headers=cand_headers)
        assert r3.status_code == 200
        d3 = r3.json()
        assert d3["needs_admin"] is True

        # 4. Admin lists customer conversations
        r4 = client.get("/api/chat/admin/conversations", headers=admin_headers)
        assert r4.status_code == 200
        convos = r4.json()["conversations"]
        cand_convo = next((c for c in convos if c["user_id"] == cand_id), None)
        assert cand_convo is not None
        assert cand_convo["user_email"] == "amina_chat_test@example.com"
        assert cand_convo["unread_count"] >= 1

        # 5. Admin retrieves conversation history
        r5 = client.get(f"/api/chat/admin/conversation/{cand_id}", headers=admin_headers)
        assert r5.status_code == 200
        d5 = r5.json()
        assert d5["user"]["email"] == "amina_chat_test@example.com"
        assert len(d5["messages"]) >= 4

        # 6. Admin replies through the platform
        reply_text = "Bonjour Amina ! Je suis l'administrateur de la plateforme. Comment puis-je vous aider ?"
        r6 = client.post("/api/chat/admin/reply", json={"target_user_id": cand_id, "message": reply_text}, headers=admin_headers)
        assert r6.status_code == 200
        d6 = r6.json()
        assert d6["message"]["sender_role"] == "admin"
        assert d6["message"]["message"] == reply_text

        # 7. Candidate retrieves updated history and sees admin's reply
        r7 = client.get("/api/chat/history", headers=cand_headers)
        assert r7.status_code == 200
        latest_msgs = r7.json()["messages"]
        admin_reply = next((m for m in latest_msgs if m["sender_role"] == "admin"), None)
        assert admin_reply is not None
        assert admin_reply["message"] == reply_text

        # 8. RBAC Security: Non-admin users cannot access admin chat routes
        r8 = client.get("/api/chat/admin/conversations", headers=cand_headers)
        assert r8.status_code == 403

        r9 = client.post("/api/chat/admin/reply", json={"target_user_id": cand_id, "message": "unauthorized"}, headers=cand_headers)
        assert r9.status_code == 403

    finally:
        # Cleanup
        with get_session() as session:
            session.query(SupportMessage).filter(SupportMessage.user_id == cand_id).delete()
            session.query(Notification).filter(Notification.user_id == cand_id).delete()
            cand_user = session.get(User, cand_id)
            if cand_user:
                session.delete(cand_user)
            session.commit()
