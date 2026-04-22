import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

with open('app.py', 'r', encoding='utf-8') as f:
    code = f.read()

# Count current occurrences
c1 = code.count("cookie_mode   = data.get('cookie_mode', 'browser')")
c2 = code.count("cookie_mode = data.get('cookie_mode', 'browser')")
print(f"Found {c1} triple-space + {c2} single-space cookie_mode='browser' defaults")

# Fix: default to 'file' 
code = code.replace(
    "cookie_mode   = data.get('cookie_mode', 'browser')",
    "cookie_mode   = data.get('cookie_mode', 'file')  # default: always use uploaded cookie"
)
code = code.replace(
    "cookie_mode = data.get('cookie_mode', 'browser')",
    "cookie_mode = data.get('cookie_mode', 'file')  # default: always use uploaded cookie"
)

# Fix: use isfile check so cookie is used whenever present regardless of mode sent
old_assign = "cookie_file = YT_COOKIES_FILE if cookie_mode == 'file' else None"
new_assign  = "cookie_file = YT_COOKIES_FILE if os.path.isfile(YT_COOKIES_FILE) else None  # always use if exists"
c3 = code.count(old_assign)
print(f"Found {c3} cookie_file assignments to fix")
code = code.replace(old_assign, new_assign)

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(code)

import subprocess, sys as _sys
r = subprocess.run([_sys.executable, '-m', 'py_compile', 'app.py'], capture_output=True, text=True)
if r.returncode == 0:
    print("[OK] app.py syntax clean")
else:
    print("[ERROR]", r.stderr)
