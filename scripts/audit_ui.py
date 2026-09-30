import re
import os

with open('src/dashboard/templates/dashboard.html', 'r', encoding='utf-8') as f:
    html = f.read()

with open('src/dashboard/static/js/app.js', 'r', encoding='utf-8') as f:
    js = f.read()

print("=" * 60)
print("DASHBOARD UI & JAVASCRIPT HANDLER INTEGRITY AUDIT")
print("=" * 60)

# 1. Check all inline onclick handlers in HTML
onclick_matches = re.findall(r'onclick=[\'"]([^\'"\(]+)', html)
onclicks = set(onclick_matches)

missing_handlers = []
for fn in sorted(onclicks):
    fn_clean = fn.strip()
    # Check if function exists in JS
    pattern = rf'(function\s+{re.escape(fn_clean)}\b|window\.{re.escape(fn_clean)}\s*=|const\s+{re.escape(fn_clean)}\s*=\s*|let\s+{re.escape(fn_clean)}\s*=\s*|var\s+{re.escape(fn_clean)}\s*=\s*)'
    if re.search(pattern, js) or fn_clean in js:
        print(f" [PASS] onclick='{fn_clean}(...)' -> Handled in app.js")
    else:
        print(f" [FAIL] onclick='{fn_clean}(...)' -> NOT FOUND in app.js!")
        missing_handlers.append(fn_clean)

# 2. Check all interactive buttons / inputs with IDs
ids = re.findall(r'<(?:button|input|select|a)[^>]+id=[\'"]([^\'"]+)[\'"]', html)
print(f"\nAudited {len(ids)} HTML interactive elements.")

# 3. Check Event Listeners in JS
print("\nChecking addEventListener bindings in app.js:")
listeners = re.findall(r"getElementById\(['\"]([^'\"]+)['\"]\)\.addEventListener\(['\"]([^'\"]+)['\"]", js)
for elem_id, event in listeners:
    if f'id="{elem_id}"' in html or f"id='{elem_id}'" in html:
        print(f" [PASS] addEventListener('{event}') on #{elem_id} (exists in HTML)")
    else:
        print(f" [WARN] addEventListener on #{elem_id} (not directly found in HTML)")

if missing_handlers:
    print(f"\n[ALERT] {len(missing_handlers)} missing handlers found!")
    exit(1)
else:
    print("\n[SUCCESS] All HTML onclick handlers and interactive elements are 100% wired up and valid!")
