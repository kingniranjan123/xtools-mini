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

    # Find the downloaded file
    mp4_path = None
    for fname in os.listdir(out_dir):
        if fname.startswith(reel_id) and fname.endswith('.mp4'):
            mp4_path = os.path.join(out_dir, fname)
            break

    # Find thumbnail
    thumbnail = None
    for fname in os.listdir(out_dir):
        if fname.startswith(reel_id) and fname.endswith('.jpg'):
            thumbnail = os.path.join(out_dir, fname)
            break

    meta = extract_metadata_from_info(raw_info)
    meta['file_path'] = mp4_path
    meta['thumbnail'] = thumbnail
    meta['account']   = account
    meta['status']    = 'ok'
    return meta
