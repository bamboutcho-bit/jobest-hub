import urllib.request
import base64
import os

BASE = os.environ.get("DASHBOARD_URL", "http://dashboard:8080")
auth = base64.b64encode(b"admin:boutcho").decode("ascii")
req = urllib.request.Request(f"{BASE}/", headers={"Authorization": f"Basic {auth}"})

try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        html = resp.read().decode("utf-8")

    checks = [
        "jHrFinderSection",
        "lookupCompanyHrEmails",
        "triggerBatchHrEnrichment",
        "Enrich HR Emails",
        "switchJobsTab('hr_finder')",
        "enrichModalJobHr()",
        "hr_verified"
    ]

    print("=== DASHBOARD HTML INTEGRITY CHECKS ===")
    for c in checks:
        present = c in html
        status = "PASS" if present else "FAIL"
        print(f"[{status}] '{c}' in HTML")

    req_js = urllib.request.Request(f"{BASE}/static/js/app.js")
    with urllib.request.urlopen(req_js, timeout=10) as resp:
        js = resp.read().decode("utf-8")

    js_checks = [
        "lookupCompanyHrEmails",
        "triggerBatchHrEnrichment",
        "enrichModalJobHr",
        "loadHrStats",
        "api/hr/discover",
        "api/hr/enrich_batch",
        "api/hr/enrich_status",
        "api/hr/stats"
    ]

    print("\n=== DASHBOARD JS INTEGRITY CHECKS ===")
    for c in js_checks:
        present = c in js
        status = "PASS" if present else "FAIL"
        print(f"[{status}] '{c}' in JS")

except Exception as e:
    print(f"Error checking dashboard: {e}")
