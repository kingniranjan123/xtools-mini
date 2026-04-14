import requests, sys, os, time

BASE = "http://localhost:5055"
s = requests.Session()
s.post(BASE + "/login", data={"password": "nikethan"}, allow_redirects=True)
print("Logged in OK")

# ======================================================
# FIX 1: Cookie Modal JS present in settings page
# ======================================================
r = s.get(BASE + "/settings")
html = r.text
print("\n=== FIX 1: Cookie Modal JS (settings.html) ===")
checks = [
    ("window.openCookieModal defined",  "window.openCookieModal"),
    ("setProperty used for display",    "setProperty"),
    ("closeCookieModal defined",        "closeCookieModal"),
    ("saveCookiePaste window-scoped",   "window.saveCookiePaste"),
]
ok1 = True
for label, needle in checks:
    found = needle in html
    ok1 = ok1 and found
    print("  [OK]  " + label if found else "  [FAIL] " + label)

# ======================================================
# FIX 2: Watermark text tab in watermark page
# ======================================================
r2 = s.get(BASE + "/watermark")
html2 = r2.text
print("\n=== FIX 2: Watermark Text Tab (watermark.html) ===")
checks2 = [
    ("tab-text div exists",     'id="tab-text"'),
    ("tab-text-btn exists",     "tab-text-btn"),
    ("wm-text-override input",  "wm-text-override"),
    ("Text Watermark label",    "Text Watermark"),
]
ok2 = True
for label, needle in checks2:
    found = needle in html2
    ok2 = ok2 and found
    print("  [OK]  " + label if found else "  [FAIL] " + label)

# ======================================================
# FIX 3: sessionid_warning in cookie save response
# ======================================================
print("\n=== FIX 3: sessionid_warning in cookie save ===")
no_sess = "# Netscape\n.instagram.com\tTRUE\t/\tTRUE\t9999\tmid\tabc123"
d3 = s.post(BASE + "/api/settings/cookies/save",
    json={"account_id": "p3", "content": no_sess},
    headers={"Content-Type": "application/json"}).json()
w1 = d3.get("sessionid_warning") == True
print(("  [OK]  " if w1 else "  [FAIL] ") + "warning=True when sessionid missing: " + str(d3))

with_sess = no_sess + "\n.instagram.com\tTRUE\t/\tTRUE\t9999\tsessionid\t12345abc"
d3b = s.post(BASE + "/api/settings/cookies/save",
    json={"account_id": "p3", "content": with_sess},
    headers={"Content-Type": "application/json"}).json()
w2 = d3b.get("sessionid_warning") == False
print(("  [OK]  " if w2 else "  [FAIL] ") + "warning=False when sessionid present: " + str(d3b))

# ======================================================
# FIX 4: Watermark text mode API returns job_id
# ======================================================
print("\n=== FIX 4: Watermark Text Mode API ===")
folder = r"D:\Downloads\test\Music_Master___Tamil_Songs"
fd = {"folder": folder, "position": "BR", "opacity": "0.85",
      "scale": "0.15", "output_mode": "new_folder",
      "mode": "text", "wm_text": "@nikethan"}
d4 = s.post(BASE + "/api/watermark/apply", data=fd).json()
ok4 = "job_id" in d4
print(("  [OK]  " if ok4 else "  [FAIL] ") + "job_id returned: " + str(d4.get("job_id", d4)))

# Wait briefly and check for watermarked output
if ok4:
    time.sleep(8)
    wm_dir = os.path.join(folder, "watermarked")
    if os.path.isdir(wm_dir):
        files = [f for f in os.listdir(wm_dir) if f.endswith(".mp4")]
        print("  [OK]  Watermarked dir has " + str(len(files)) + " mp4 file(s)")
    else:
        print("  [WAIT] watermarked/ dir not yet created after 8s (FFmpeg may still be running)")

print("\n=== Summary ===")
all_ok = ok1 and ok2 and w1 and w2 and ok4
print("ALL FIXES VERIFIED" if all_ok else "SOME FIXES FAILED — see above")
