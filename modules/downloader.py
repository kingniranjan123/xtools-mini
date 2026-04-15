"""
Instagram Reels downloader — yt-dlp backed
"""
import os, re, json, subprocess, shutil, sys
from modules.metadata import extract_metadata_from_info

def download_reels(urls, quality, cookies_file, downloads_dir,
                   custom_dir=False, progress_cb=None, db_cb=None):
    """
    Download a list of Instagram Reel URLs using yt-dlp.
    Returns list of result dicts.
    """
    results = []
    total   = len(urls)

    for idx, url in enumerate(urls, 1):
        url = url.strip()
        if not url:
            continue

        pct_base = int((idx - 1) / total * 100)
        if progress_cb:
            progress_cb(f'[{idx}/{total}] Starting: {url}', pct_base)

        try:
            info = _download_single(url, quality, cookies_file, downloads_dir, custom_dir, progress_cb, pct_base, total)
            if info:
                results.append(info)
                if db_cb:
                    db_cb(info)
                if progress_cb:
                    progress_cb(f'✓ Downloaded: {info.get("title") or info["id"]}',
                                int(idx / total * 100))
            else:
                results.append({'url': url, 'status': 'error', 'error': 'No info returned'})
        except Exception as exc:
            if progress_cb:
                progress_cb(f'✗ Error: {exc}')
            results.append({'url': url, 'status': 'error', 'error': str(exc)})

    return results


def _download_single(url, quality, cookies_file, downloads_dir, custom_dir, progress_cb, pct_base, total):
    """Run yt-dlp for one URL, return info dict."""
    ytdlp = [sys.executable, '-m', 'yt_dlp', '--rm-cache-dir']

    # First: extract info (no download) to get the account name for folder
    info_cmd = ytdlp + ['--print-json', '--no-download', url]
    if cookies_file and os.path.isfile(cookies_file):
        info_cmd += ['--cookies', cookies_file]

    raw_info_text = subprocess.check_output(
        info_cmd, stderr=subprocess.DEVNULL, text=True, encoding='utf-8', errors='replace', timeout=60
    )
    raw_info = json.loads(raw_info_text)

    account = raw_info.get('uploader_id') or raw_info.get('uploader') or 'unknown'
    account = re.sub(r'[^\w.-]', '_', account)  # sanitise folder name

    if custom_dir:
        out_dir = downloads_dir
    else:
        out_dir = os.path.join(downloads_dir, account)
    os.makedirs(out_dir, exist_ok=True)

    # Format string
    if quality == 'best':
        fmt = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
    else:
        fmt = f'bestvideo[height<={quality}][ext=mp4]+bestaudio[ext=m4a]/best[height<={quality}][ext=mp4]/best'

    reel_id = raw_info.get('id', 'unknown')
    out_template = os.path.join(out_dir, '%(title).150s.%(ext)s')

    files_before = set(os.listdir(out_dir))

    dl_cmd = ytdlp + [
        '--format', fmt,
        '--output', out_template,
        '--write-info-json',
        '--write-thumbnail',
        '--convert-thumbnails', 'jpg',
        '--no-playlist',
        url,
    ]
    if cookies_file and os.path.isfile(cookies_file):
        dl_cmd += ['--cookies', cookies_file]

    proc = subprocess.Popen(
        dl_cmd,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding='utf-8', errors='replace', bufsize=1
    )

    for line in proc.stdout:
        line = line.rstrip()
        if progress_cb and line:
            # Parse yt-dlp's [download] X% line
            m = re.search(r'\[download\]\s+([\d.]+)%', line)
            pct = None
            if m:
                dl_pct = float(m.group(1))
                pct = pct_base + int(dl_pct / total)
            progress_cb(line, pct)

    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f'yt-dlp exited with code {proc.returncode}')

    files_after = set(os.listdir(out_dir))
    new_files = files_after - files_before

    # Find downloaded video and thumbnail robustly.
    # Older builds used reel_id-prefixed names; newer templates are title-based.
    mp4_path = _find_downloaded_video(
        out_dir=out_dir,
        reel_id=reel_id,
        raw_info=raw_info,
        new_files=new_files
    )
    thumbnail = _find_downloaded_thumbnail(
        out_dir=out_dir,
        reel_id=reel_id,
        raw_info=raw_info,
        new_files=new_files
    )

    meta = extract_metadata_from_info(raw_info)
    meta['file_path'] = mp4_path
    meta['thumbnail'] = thumbnail
    meta['account']   = account
    meta['status']    = 'ok'
    return meta


def _find_downloaded_video(out_dir, reel_id, raw_info, new_files):
    """Best-effort resolver for downloaded video path across naming formats."""
    allowed_video_exts = ('.mp4', '.mov', '.mkv', '.webm', '.m4v')

    # 1) Prefer newly created files from this download invocation.
    new_video_candidates = [
        os.path.join(out_dir, fn) for fn in new_files
        if fn.lower().endswith(allowed_video_exts)
    ]
    new_video_candidates = [p for p in new_video_candidates if os.path.isfile(p)]
    if new_video_candidates:
        return max(new_video_candidates, key=os.path.getmtime)

    # 2) Match explicit final filename from yt-dlp metadata if available.
    requested = raw_info.get('requested_downloads') or []
    for item in requested:
        fp = item.get('_filename') or item.get('filepath')
        if fp and os.path.isfile(fp) and fp.lower().endswith(allowed_video_exts):
            return fp

    # 3) Backward compatibility: previously stored as reel_id-prefixed .mp4 files.
    for fname in os.listdir(out_dir):
        if fname.startswith(reel_id) and fname.lower().endswith('.mp4'):
            return os.path.join(out_dir, fname)

    # 4) Last-chance fallback: newest video file in the output folder.
    all_videos = [
        os.path.join(out_dir, fn) for fn in os.listdir(out_dir)
        if fn.lower().endswith(allowed_video_exts)
    ]
    all_videos = [p for p in all_videos if os.path.isfile(p)]
    if all_videos:
        return max(all_videos, key=os.path.getmtime)
    return None


def _find_downloaded_thumbnail(out_dir, reel_id, raw_info, new_files):
    """Best-effort resolver for downloaded thumbnail path."""
    # 1) Prefer newly created jpg thumbnails from this invocation.
    new_thumbs = [
        os.path.join(out_dir, fn) for fn in new_files
        if fn.lower().endswith('.jpg')
    ]
    new_thumbs = [p for p in new_thumbs if os.path.isfile(p)]
    if new_thumbs:
        return max(new_thumbs, key=os.path.getmtime)

    # 2) Backward compatibility: reel_id-prefixed jpg files.
    for fname in os.listdir(out_dir):
        if fname.startswith(reel_id) and fname.lower().endswith('.jpg'):
            return os.path.join(out_dir, fname)

    # 3) Last-chance fallback: newest jpg in folder.
    all_jpgs = [
        os.path.join(out_dir, fn) for fn in os.listdir(out_dir)
        if fn.lower().endswith('.jpg')
    ]
    all_jpgs = [p for p in all_jpgs if os.path.isfile(p)]
    if all_jpgs:
        return max(all_jpgs, key=os.path.getmtime)
    return None
