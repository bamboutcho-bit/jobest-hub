"""Token generation utility for AutoHunt & n8n integration.

Generates:
1. N8N Webhook Secret Token (for n8n to authenticate to AutoHunt webhook endpoints)
2. User API Session Token (signed HMAC token for n8n or external scripts to call /api/* as admin)

Usage:
    python scripts/generate_token.py [--update-env] [--user-id 1]
"""
import argparse
import secrets
import sys
from pathlib import Path

# Add project root to sys.path
root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir))

def generate_webhook_secret(length: int = 32) -> str:
    """Generate a high-entropy URL-safe secret token for n8n webhook authentication."""
    return secrets.token_urlsafe(length)

def generate_session_token(user_id: int = 1, email: str = "admin@autohunt.internal", role: str = "admin") -> str:
    """Generate an AutoHunt signed session bearer token."""
    try:
        from src.auth.service import create_session_token
        return create_session_token(user_id=user_id, email=email, role=role)
    except Exception as e:
        return f"Error creating session token: {e}"

def update_env_file(new_secret: str) -> bool:
    """Safely update N8N_WEBHOOK_SECRET in .env file."""
    env_path = root_dir / ".env"
    if not env_path.exists():
        print(f"Warning: {env_path} does not exist.")
        return False

    content = env_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    updated = False

    for i, line in enumerate(lines):
        if line.startswith("N8N_WEBHOOK_SECRET="):
            lines[i] = f"N8N_WEBHOOK_SECRET={new_secret}"
            updated = True
            break

    if not updated:
        lines.append(f"N8N_WEBHOOK_SECRET={new_secret}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True

def main():
    parser = argparse.ArgumentParser(description="Generate tokens for AutoHunt & n8n")
    parser.add_argument("--update-env", action="store_true", help="Automatically update N8N_WEBHOOK_SECRET in .env")
    parser.add_argument("--user-id", type=int, default=1, help="User ID for the session token (default: 1)")
    parser.add_argument("--email", type=str, default="admin@autohunt.internal", help="Email for the session token")
    parser.add_argument("--role", type=str, default="admin", help="Role for the session token (default: admin)")
    args = parser.parse_args()

    webhook_secret = generate_webhook_secret()
    session_token = generate_session_token(args.user_id, args.email, args.role)

    print("\n" + "=" * 64)
    print("🔑 AUTOHUNT & N8N TOKEN GENERATOR")
    print("=" * 64)
    print("\n1. N8N WEBHOOK SECRET TOKEN (for n8n -> AutoHunt Webhooks):")
    print(f"   {webhook_secret}")
    print("\n   Usage in n8n HTTP Request Header:")
    print("   Header Name:  x-webhook-secret")
    print(f"   Header Value: {webhook_secret}")

    print("\n" + "-" * 64)
    print("2. USER API BEARER TOKEN (for full /api/* access):")
    print(f"   {session_token}")
    print("\n   Usage in n8n or Curl:")
    print("   Header Name:  Authorization")
    print(f"   Header Value: Bearer {session_token}")
    print("=" * 64 + "\n")

    if args.update_env:
        if update_env_file(webhook_secret):
            print(f"✅ Successfully updated N8N_WEBHOOK_SECRET in {root_dir / '.env'}")
            print("ℹ️  Remember to run: docker compose restart dashboard app")
        else:
            print("❌ Failed to update .env automatically.")

if __name__ == "__main__":
    main()
