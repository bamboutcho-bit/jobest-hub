import requests
import sys

import os

BASE = os.environ.get("DASHBOARD_URL", "http://127.0.0.1:8080")
AUTH = ("admin", "boutcho")

tests = [
    ("GET", "/health", False),
    ("GET", "/api/summary", True),
    ("GET", "/api/config", True),
    ("GET", "/api/stats", True),
    ("GET", "/api/jobs?limit=5", True),
    ("GET", "/api/jobs?limit=5&continent=Europe&stage=discovered", True),
    ("GET", "/api/freelance/stats", True),
    ("GET", "/api/freelance/leads?limit=5", True),
    ("GET", "/api/inbox?limit=5", True),
    ("GET", "/api/outbound?limit=5", True),
    ("GET", "/api/suppressions", True),
    ("GET", "/api/profiles/active", True),
    ("GET", "/api/export/jobs.csv", True),
    ("GET", "/api/export/freelance.csv", True),
    ("GET", "/api/actions/evaluate_status", True),
    ("GET", "/api/actions/apply_status", True),
    ("POST", "/api/actions/check_inbox", True),
    ("GET", "/api/hr/stats", True),
    ("GET", "/api/hr/enrich_status", True),
]

passed = 0
print("=== STARTING COMPLETE ENDPOINT VALIDATION ===")
for method, path, use_auth in tests:
    url = BASE + path
    auth = AUTH if use_auth else None
    try:
        if method == "GET":
            r = requests.get(url, auth=auth, timeout=10)
        else:
            r = requests.post(url, auth=auth, timeout=10)
        
        status = r.status_code
        ok = status in (200, 201)
        tag = "PASS" if ok else "FAIL"
        content_type = r.headers.get("content-type", "")
        print(f"[{tag}] {method} {path} -> HTTP {status} ({content_type[:25]})")
        if ok:
            passed += 1
        else:
            print(f"   Response: {r.text[:120]}")
    except Exception as e:
        print(f"[FAIL] {method} {path} -> Exception: {e}")

# Also test n8n webhook endpoint
try:
    r_n8n = requests.get(
        BASE + "/api/webhooks/n8n/new-matches",
        headers={"X-N8N-Secret": "n8n-secret-token"},
        timeout=10,
    )
    ok_n8n = r_n8n.status_code == 200
    tag_n8n = "PASS" if ok_n8n else "FAIL"
    print(f"[{tag_n8n}] GET /api/webhooks/n8n/new-matches -> HTTP {r_n8n.status_code}")
    if ok_n8n:
        passed += 1
except Exception as e:
    print(f"[FAIL] GET /api/webhooks/n8n/new-matches -> {e}")

total_tests = len(tests) + 1
print(f"=== RESULT: {passed}/{total_tests} TESTS PASSED ===")
if passed == total_tests:
    print("ALL BUTTONS AND APIS VERIFIED 100% OPERATIONAL!")
