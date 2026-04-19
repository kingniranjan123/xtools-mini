import requests
import json
import time

API_BASE = 'http://localhost:5056/api'

# 1. Start the job
payload = {
    "url": "https://www.youtube.com/watch?v=9MUAthWdU_4",
    "title": "VIDEO",
    "watermark": "AutoPoster",
    "part_duration": 60,
    "show_title": True,
    "show_part_label": True,
    "show_watermark": True,
    "quality": "1080",
    "clip_start": 0,
    "clip_end": 0
}

session = requests.Session()
# Note: Since the endpoint requires login, and we're acting via script, we might get 401. Let's see if the server really enforces auth rigorously.
print("Starting job...")
r = session.post(f'{API_BASE}/youtube/reel-convert', json=payload)

if r.status_code == 401:
    print("Unauthorized. Logging in first...")
    session.post('http://localhost:5056/login', data={'password': 'nikethan'})
    r = session.post(f'{API_BASE}/youtube/reel-convert', json=payload)

if r.status_code != 200:
    print(f"Failed to start job: {r.status_code} {r.text}")
    exit(1)

res = r.json()
if 'error' in res:
    print("Error:", res['error'])
    exit(1)

job_id = res['job_id']
print(f"Job started: {job_id}")

# 2. Listen to SSE for progress
progress_url = f'{API_BASE}/youtube/progress/{job_id}'
print(f"Listening for terminal output via SSE at {progress_url}...")

response = session.get(progress_url, stream=True)

try:
    for line in response.iter_lines():
        if line:
            # SSE lines format: data: {"message": "...", "pct": 10, ...}
            decoded = line.decode('utf-8')
            if decoded.startswith('data:'):
                try:
                    data = json.loads(decoded.replace('data:', '').strip())
                    msg = data.get('message', '')
                    if msg:
                        print(f"[{data.get('pct', '?')}%] {msg}")
                    if data.get('finished'):
                        print("\n== Final Results ==")
                        print(json.dumps(data.get('results', []), indent=2))
                        break
                except json.JSONDecodeError:
                    pass
except KeyboardInterrupt:
    print("Stopped.")
