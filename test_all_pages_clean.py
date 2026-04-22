import sys
from app import app

app.config['TESTING'] = True
client = app.test_client()

# Bypass login for testing
with client.session_transaction() as sess:
    sess['logged_in'] = True

routes_to_test = []
for rule in app.url_map.iter_rules():
    # Only test GET routes without variables (no <id>)
    if 'GET' in rule.methods and '<' not in rule.rule:
        # Exclude static routes, api routes, and streaming routes
        if rule.endpoint != 'static' and '/api/' not in rule.rule and '/stream/' not in rule.rule:
            routes_to_test.append(rule.rule)

print(f"Testing {len(routes_to_test)} UI Page routes...\n")

failed = 0
for route in sorted(routes_to_test):
    try:
        response = client.get(route)
        status = response.status_code
        if status in (200, 302):
            print(f"[\033[92mPASS\033[0m] {status} - {route}")
        else:
            print(f"[\033[91mFAIL\033[0m] {status} - {route}")
            failed += 1
    except Exception as e:
        print(f"[\033[91mCRASH\033[0m] {route} -> {str(e)}")
        failed += 1

print(f"\nDone. Failed/Crashed: {failed}")
