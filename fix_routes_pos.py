import os

file_path = "app.py"
with open(file_path, "r", encoding="utf-8") as f:
    lines = f.readlines()

# Find the start of the appended routes
start_routes = -1
for i, line in enumerate(lines):
    if "def bulk_local_page():" in line:
        start_routes = i - 4 # To include the comment block
        break

if start_routes != -1 and start_routes > len(lines) - 200:
    routes_code = lines[start_routes:]
    lines = lines[:start_routes]

    # Find where to insert it (e.g. right before "# Pre-populate P1-P5 in DB" or the bottom-most route)
    insert_idx = -1
    for i, line in enumerate(lines):
        if "from modules.account_manager import ensure_default_profiles_exist" in line or "# Pre-populate P1-P5 in DB" in line:
            insert_idx = i
            break

    if insert_idx != -1:
        lines = lines[:insert_idx] + routes_code + ["\n"] + lines[insert_idx:]
        with open(file_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        print("Routes successfully moved before app.run")
    else:
        print("Could not find insert_idx")
else:
    print("Could not find start_routes or it's already moved.")
