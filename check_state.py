checks = {
    'reel_converter fontfile fix': ('modules/reel_converter.py', '_resolve_font'),
    'splitter UTF-8 encoding fix': ('modules/splitter.py', 'utf-8'),
    'downloader UTF-8 encoding fix': ('modules/downloader.py', 'utf-8'),
    'youtube_downloader UTF-8 fix': ('modules/youtube_downloader.py', 'utf-8'),
    'test-key NoneType fix': ('app.py', 'data.get('),
    'threading app_context fix': ('app.py', 'app.app_context()'),
    'infra test endpoint': ('app.py', 'test-infra'),
    'Test Infrastructure tab': ('templates/settings.html', 'settings-tab-infra'),
    'MODEL_CANDIDATES AI fallback': ('modules/ai_generator.py', 'MODEL_CANDIDATES'),
    '_ensure_instagrapi guard': ('app.py', '_ensure_instagrapi'),
    'initial-setup endpoint': ('app.py', 'initial-setup'),
    'instagrapi in requirements': ('requirements.txt', 'instagrapi'),
}
for name, (fp, needle) in checks.items():
    try:
        with open(fp, 'r', encoding='utf-8', errors='replace') as f:
            found = needle in f.read()
        print(f'  {"OK  " if found else "MISS"}: {name}')
    except FileNotFoundError:
        print(f'  NOFIL: {fp}')
