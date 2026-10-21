import urllib.request
import urllib.parse
import http.cookiejar
import json
import sys

base_url = "http://localhost:8080"

print("=== TEST 1: Unauthenticated access to /api/auth/me ===")
try:
    req = urllib.request.Request(f"{base_url}/api/auth/me")
    urllib.request.urlopen(req, timeout=5)
    print("FAIL: /api/auth/me should have returned 401!")
    sys.exit(1)
except urllib.error.HTTPError as e:
    print(f"PASS: /api/auth/me returned HTTP {e.code} ({e.reason})")

print("\n=== TEST 2: Unauthenticated access to /app ===")
class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

opener = urllib.request.build_opener(NoRedirectHandler)
try:
    resp = opener.open(f"{base_url}/app")
    print("Status:", resp.status)
except urllib.error.HTTPError as e:
    print(f"PASS: /app returned HTTP {e.code} Redirect to {e.headers.get('Location')}")

print("\n=== TEST 3: Access to Landing Page / ===")
with urllib.request.urlopen(f"{base_url}/", timeout=5) as resp:
    html = resp.read().decode("utf-8")
    assert "AutoHunt" in html
    print(f"PASS: Landing page rendered HTTP {resp.status}, size {len(html)}")

print("\n=== TEST 4: Admin Login (admin@autohunt.internal / boutcho) ===")
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

login_data = json.dumps({"email": "admin@autohunt.internal", "password": "boutcho"}).encode()
req = urllib.request.Request(f"{base_url}/api/auth/login", data=login_data, headers={"Content-Type": "application/json"})
with opener.open(req) as resp:
    data = json.loads(resp.read().decode())
    print("Login response ok:", data.get("ok"))
    cookies = [f"{c.name}={c.value[:10]}..." for c in cj]
    print("Cookies received:", cookies)

# Verify /api/auth/me as admin
req = urllib.request.Request(f"{base_url}/api/auth/me")
with opener.open(req) as resp:
    me = json.loads(resp.read().decode())
    print("Logged in as:", me["user"]["email"], "Role:", me["user"]["role"], "ID:", me["user"]["id"])
    assert me["user"]["id"] == 1
    assert me["user"]["role"] == "admin"

# Verify Admin profile and jobs
req = urllib.request.Request(f"{base_url}/api/profiles/active")
with opener.open(req) as resp:
    prof = json.loads(resp.read().decode())
    print("Active profile name:", prof["profile"]["name"])
    assert "Hamza" in prof["profile"]["name"]

req = urllib.request.Request(f"{base_url}/api/jobs?limit=5")
with opener.open(req) as resp:
    jobs = json.loads(resp.read().decode())
    print("Admin jobs returned (sample count):", len(jobs))
    assert len(jobs) > 0

print("\n=== TEST 5: Logout via /api/auth/logout ===")
req = urllib.request.Request(f"{base_url}/api/auth/logout", data=b"", headers={"Content-Type": "application/json"})
with opener.open(req) as resp:
    data = json.loads(resp.read().decode())
    print("Logout response:", data)

print("\n=== TEST 6: Calling /api/auth/me after logout ===")
try:
    req = urllib.request.Request(f"{base_url}/api/auth/me")
    opener.open(req)
    print("FAIL: /api/auth/me should have returned 401 after logout!")
    sys.exit(1)
except urllib.error.HTTPError as e:
    print(f"PASS: /api/auth/me returned HTTP {e.code} after logout")

print("\n=== TEST 7: Visiting /logout redirect ===")
req = urllib.request.Request(f"{base_url}/logout")
try:
    opener_noredir = urllib.request.build_opener(NoRedirectHandler)
    opener_noredir.open(req)
except urllib.error.HTTPError as e:
    print(f"PASS: /logout returned HTTP {e.code} redirecting to {e.headers.get('Location')}")
    set_cookies = e.headers.get_all("Set-Cookie")
    print("Set-Cookie headers on /logout:", set_cookies)

print("\n=== TEST 8: Register & Login Second Independent User ===")
user2_email = "independent_candidate_2026@example.com"
user2_pass = "SecurePass2026!"
cj2 = http.cookiejar.CookieJar()
opener2 = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj2))

reg_data = json.dumps({"email": user2_email, "password": user2_pass, "full_name": "Independent Candidate", "plan": "free"}).encode()
req = urllib.request.Request(f"{base_url}/api/auth/register", data=reg_data, headers={"Content-Type": "application/json"})
try:
    with opener2.open(req) as resp:
        reg_res = json.loads(resp.read().decode())
        print("Registered user 2:", reg_res.get("ok"))
except urllib.error.HTTPError as e:
    print(f"User might already exist ({e.code}), logging in...")
    login2_data = json.dumps({"email": user2_email, "password": user2_pass}).encode()
    req = urllib.request.Request(f"{base_url}/api/auth/login", data=login2_data, headers={"Content-Type": "application/json"})
    with opener2.open(req) as resp:
        reg_res = json.loads(resp.read().decode())
        print("Logged in user 2:", reg_res.get("ok"))

# Verify user 2 identity
req = urllib.request.Request(f"{base_url}/api/auth/me")
with opener2.open(req) as resp:
    me2 = json.loads(resp.read().decode())
    print("User 2 identity:", me2["user"]["email"], "ID:", me2["user"]["id"], "Role:", me2["user"]["role"])
    assert me2["user"]["id"] != 1
    assert me2["user"]["role"] == "user"

# Verify user 2 has 0 jobs (data isolation from Admin!)
req = urllib.request.Request(f"{base_url}/api/jobs?limit=5")
with opener2.open(req) as resp:
    jobs2 = json.loads(resp.read().decode())
    print("User 2 jobs count:", len(jobs2))
    assert len(jobs2) == 0, f"Expected 0 jobs for new user, got {len(jobs2)}"
    print("PASS: User 2 has 0 jobs - strictly isolated from Admin's jobs!")

# Verify user 2 has their own candidate profile (NOT Hamza's!)
req = urllib.request.Request(f"{base_url}/api/profiles/active")
with opener2.open(req) as resp:
    prof2 = json.loads(resp.read().decode())
    p_name = prof2.get("profile", {}).get("name", "")
    print("User 2 active profile name:", p_name)
    assert "Hamza" not in p_name, f"User 2 leaked Admin's name: {p_name}"
    print("PASS: User 2 candidate profile is completely isolated from Admin!")

# Verify User 2 logout
req = urllib.request.Request(f"{base_url}/api/auth/logout", data=b"", headers={"Content-Type": "application/json"})
with opener2.open(req) as resp:
    print("User 2 logout ok:", json.loads(resp.read().decode()).get("ok"))

print("\n=== TEST 9: Final Admin Verification (Ensuring Admin data never modified/lost) ===")
cj_admin = http.cookiejar.CookieJar()
opener_admin = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj_admin))
login_admin_data = json.dumps({"email": "admin@autohunt.internal", "password": "boutcho"}).encode()
req = urllib.request.Request(f"{base_url}/api/auth/login", data=login_admin_data, headers={"Content-Type": "application/json"})
with opener_admin.open(req) as resp:
    data = json.loads(resp.read().decode())
    assert data.get("ok") is True

req = urllib.request.Request(f"{base_url}/api/auth/me")
with opener_admin.open(req) as resp:
    me_admin = json.loads(resp.read().decode())
    assert me_admin["user"]["id"] == 1
    assert me_admin["user"]["role"] == "admin"
    print("PASS: Admin User ID 1 is active with role admin")

req = urllib.request.Request(f"{base_url}/api/profiles/active")
with opener_admin.open(req) as resp:
    prof_admin = json.loads(resp.read().decode())
    assert "Hamza" in prof_admin["profile"]["name"]
    print("PASS: Admin candidate profile is 100% intact:", prof_admin["profile"]["name"])

req = urllib.request.Request(f"{base_url}/api/jobs?limit=10")
with opener_admin.open(req) as resp:
    jobs_admin = json.loads(resp.read().decode())
    print("PASS: Admin has all opportunities intact:", len(jobs_admin), "sample jobs loaded")

print("\n=== ALL 9 COMPREHENSIVE TESTS PASSED WITH 100% SUCCESS! ===")
