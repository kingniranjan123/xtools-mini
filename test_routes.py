import sys
import os
from app import app

# Create a test client
app.config['TESTING'] = True
client = app.test_client()

# Bypass login for testing
with client.session_transaction() as sess:
    sess['logged_in'] = True

# Get all registered GET routes
routes_to_test = []
for rule in app.url_map.iter_rules():
    if 'GET' in rule.methods and not '<' in rule.rule:
        # Exclude static routes
        if rule.endpoint != 'static':
            routes_to_test.append((rule.rule, rule.endpoint))

print(f"Testing {len(routes_to_test)} GET endpoints...\n")

failed = 0
for route, endpoint in sorted(routes_to_test):
    try:
        response = client.get(route)
        status = response.status_code
        if status in (200, 302, 304, 204):
            print(f"[\033[92mPASS\033[0m] {status} - {route} ({endpoint})")
        else:
            print(f"[\033[91mFAIL\033[0m] {status} - {route} ({endpoint})")
            failed += 1
    except Exception as e:
        print(f"[\033[91mCRASH\033[0m] {route} ({endpoint}) -> {str(e)}")
        failed += 1

print(f"\nDone. Failed/Crashed: {failed}")
