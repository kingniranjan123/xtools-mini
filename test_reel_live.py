import sys, os
# Force UTF-8 output on Windows terminal
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, r'd:\Desktop\13th August 2023\python-output\python-inputs\a-process-telegram-uploads\xtools-mini')
from modules.youtube_downloader import download_youtube
from modules.reel_converter import convert_to_reels

BASE       = r'd:\Desktop\13th August 2023\python-output\python-inputs\a-process-telegram-uploads\xtools-mini'
YT_COOKIES = r'D:\Downloads\kingniranjan123-2.txt'
OUT        = os.path.join(BASE, 'test_reels_out')
REEL_OUT   = os.path.join(BASE, 'test_reels_out', '_reels')
os.makedirs(OUT, exist_ok=True)
os.makedirs(REEL_OUT, exist_ok=True)

URL = 'https://www.youtube.com/watch?v=bS5P_LAqiVg'

def cb(line, pct=None):
    pct_str = f' [{pct}%]' if pct is not None else ''
    print(f'{line}{pct_str}', flush=True)

print('=== STEP 1: DOWNLOAD ===')
print(f'Cookie exists: {os.path.isfile(YT_COOKIES)}')

results = download_youtube(
    urls=[URL],
    quality='720',
    output_dir=OUT,
    audio_only=False,
    custom_dir=True,
    cookie_file=YT_COOKIES,
    request_delay=0,
    progress_cb=cb
)

print('\nDownload result:', results)

if not results or results[0].get('status') != 'ok':
    err = results[0].get('error', 'Unknown') if results else 'No result'
    print(f'DOWNLOAD FAILED: {err}')
    sys.exit(1)

video_path = results[0].get('file_path') or results[0].get('filepath')
print(f'\nDownloaded to: {video_path}')

if not video_path or not os.path.isfile(video_path):
    # Scan OUT dir for mp4
    for f in os.listdir(OUT):
        if f.endswith('.mp4'):
            video_path = os.path.join(OUT, f)
            print(f'Found via scan: {video_path}')
            break

print('\n=== STEP 2: REEL CONVERT (60s parts) ===')
conv = convert_to_reels(
    input_path=video_path,
    output_dir=REEL_OUT,
    title='Test Reel',
    watermark='',
    part_duration_sec=60,
    show_title=True,
    show_part_label=True,
    show_watermark=False,
    output_size='instagram',
    progress_cb=lambda line: cb(line)
)

print('\n=== RESULT ===')
print('Parts:', conv.get('parts', []))
print('Errors:', conv.get('errors', []))
print(f'DONE: {len(conv.get("parts",[]))} parts created in {REEL_OUT}')
