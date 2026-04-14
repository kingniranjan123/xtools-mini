import sys
sys.stdout.reconfigure(encoding='utf-8')

# Check settings.html
with open('templates/settings.html', encoding='utf-8') as f:
    settings_txt = f.read()

print("=== settings.html checks ===")
print("window.openCookieModal:", "window.openCookieModal" in settings_txt)
print("setProperty:", "setProperty" in settings_txt)
print("closeCookieModal:", "closeCookieModal" in settings_txt)
print("saveCookiePaste window:", "window.saveCookiePaste" in settings_txt)

# Show a snippet around the script block
idx = settings_txt.find("window.openCookieModal")
if idx >= 0:
    print("  Found at char:", idx)
    print("  Context:", repr(settings_txt[idx:idx+60]))
else:
    # Find the script tag in modals block
    idx2 = settings_txt.find("Cookie Manager")
    print("  'Cookie Manager' found at:", idx2)
    if idx2 >= 0:
        print("  Context:", repr(settings_txt[idx2:idx2+120]))

# Check watermark.html
with open('templates/watermark.html', encoding='utf-8') as f:
    wm_txt = f.read()

print("\n=== watermark.html checks ===")
print("tab-text div:", 'id="tab-text"' in wm_txt)
print("tab-text-btn:", "tab-text-btn" in wm_txt)
print("wm-text-override:", "wm-text-override" in wm_txt)
print("Text Watermark:", "Text Watermark" in wm_txt)

idx3 = wm_txt.find("tab-text")
if idx3 >= 0:
    print("  'tab-text' found at char:", idx3)
    print("  Context:", repr(wm_txt[idx3:idx3+60]))
