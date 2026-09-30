import urllib.request
import json
import http.cookiejar

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

# Try logging in as admin or test user
login_data = json.dumps({'email': 'admin@autohunt.local', 'password': 'AdminPassword123!'}).encode()
req = urllib.request.Request('http://localhost:8080/api/auth/login', data=login_data, headers={'Content-Type': 'application/json'})
try:
    with opener.open(req) as resp:
        print('Login status:', resp.status)
        data = json.loads(resp.read().decode())
        print('User:', data.get('user', {}).get('email'), 'Role:', data.get('user', {}).get('role'), 'Plan:', data.get('user', {}).get('current_plan'))
except Exception as e:
    print('Login error:', e)

# Fetch /app
try:
    with opener.open('http://localhost:8080/app') as resp:
        html = resp.read().decode()
        print('App page size:', len(html))
        print('Has jPricingSection:', 'id="jPricingSection"' in html)
        print('Has pricingCardsGrid:', 'id="pricingCardsGrid"' in html)
        print('Has paymentModal:', 'id="paymentModal"' in html)
        print('Has jTabPricing:', 'id="jTabPricing"' in html)
        print('Has fTabPricing:', 'id="fTabPricing"' in html)
except Exception as e:
    print('App fetch error:', e)

# Test /api/payments/methods
try:
    with opener.open('http://localhost:8080/api/payments/methods') as resp:
        print('Methods status:', resp.status)
except Exception as e:
    print('Methods error:', e)

# Test /api/payments/my-payments
try:
    with opener.open('http://localhost:8080/api/payments/my-payments') as resp:
        res = json.loads(resp.read().decode())
        print('My payments count:', len(res.get('payments', [])))
except Exception as e:
    print('My-payments error:', e)

# Test /api/admin/payments
try:
    with opener.open('http://localhost:8080/api/admin/payments') as resp:
        res = json.loads(resp.read().decode())
        print('Admin payments count:', len(res.get('payments', [])))
except Exception as e:
    print('Admin-payments error:', e)
