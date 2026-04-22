import sys
from app import app

app.config['TESTING'] = True
client = app.test_client()

# Bypass login for testing
with client.session_transaction() as sess:
    sess['logged_in'] = True

pages = [
    '/',
    '/telegram',
    '/youtube',
    '/settings',
    '/process/bulk-local'
]

print("Testing Main UI Pages...\n")

failed = 0
for route in pages:
    try:
        response = client.get(route)
        status = response.status_code
        if status in (200, 302, 304, 204):
            print(f"[\033[92mPASS\033[0m] {status} - {route}")
        else:
            print(f"[\033[91mFAIL\033[0m] {status} - {route}")
            failed += 1
    except Exception as e:
        print(f"[\033[91mCRASH\033[0m] {route} -> {str(e)}")
        failed += 1

print(f"\nDone. Failed/Crashed: {failed}")
