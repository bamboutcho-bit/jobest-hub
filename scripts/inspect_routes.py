import re

with open('src/dashboard/app.py', 'r', encoding='utf-8') as f:
    code = f.read()

routes = re.findall(r'@app\.(?:get|post)\([\"\']([^\"\']+)[\"\']', code)
print(f"Total routes: {len(routes)}")
for r in routes:
    if not r.startswith('/api'):
        print("Page route:", r)
