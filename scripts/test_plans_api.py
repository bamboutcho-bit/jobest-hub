import requests

BASE = "http://127.0.0.1:8080"
AUTH = ("admin", "boutcho")

# 1. Test Admin Plans List
r_plans = requests.get(f"{BASE}/api/admin/plans", auth=AUTH)
print("GET /api/admin/plans:", r_plans.status_code)
if r_plans.status_code == 200:
    plans = r_plans.json().get("plans", [])
    print(f"Total admin plans: {len(plans)}")
    for p in plans:
        print(f"  [{p['slug']}] '{p['name']}': {p['price_mad']} MAD / ${p['price_usd']} USD | Limit: {p['daily_apply_limit']} apps/day | Subscribers: {p['subscriber_count']}")
else:
    print(r_plans.text)

# 2. Test Admin Gateways List
r_gw = requests.get(f"{BASE}/api/admin/gateways", auth=AUTH)
print("\nGET /api/admin/gateways:", r_gw.status_code)
if r_gw.status_code == 200:
    gateways = r_gw.json().get("gateways", [])
    print(f"Total admin gateways: {len(gateways)}")
    for g in gateways:
        print(f"  [{g['gateway_key']}] '{g['name']}' (enabled={g['is_enabled']}) - Cat: {g['category']}")
else:
    print(r_gw.text)

# 3. Test Updating a Plan
if r_plans.status_code == 200 and plans:
    starter_plan = next((p for p in plans if p['slug'] == 'starter'), plans[0])
    print(f"\nTesting PUT /api/admin/plans/{starter_plan['id']}...")
    update_res = requests.put(
        f"{BASE}/api/admin/plans/{starter_plan['id']}",
        json={"price_mad": 199, "price_usd": 19.0, "daily_apply_limit": 60, "sync_users": True},
        auth=AUTH
    )
    print("Update response:", update_res.status_code, update_res.json())

    # Verify update reflected in public /api/payments/methods
    pub_res = requests.get(f"{BASE}/api/payments/methods")
    pub_plans = pub_res.json().get("plans", [])
    updated_starter = next((p for p in pub_plans if p['slug'] == 'starter'), None)
    print("Public updated starter plan:", updated_starter)

    # Revert back to 150 MAD / $15 / 50 apps for clean baseline
    requests.put(
        f"{BASE}/api/admin/plans/{starter_plan['id']}",
        json={"price_mad": 150, "price_usd": 15.0, "daily_apply_limit": 50, "sync_users": True},
        auth=AUTH
    )
    print("Reverted to default clean starter plan.")
