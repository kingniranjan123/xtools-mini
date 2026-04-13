"""
YouTube Downloader — yt-dlp backed
Supports: multiple URLs, quality selection, MP3-only mode
"""
import os, re, json, subprocess, shutil, sys

def download_youtube(urls: list, quality: str, output_dir: str,
                     audio_only: bool = False, progress_cb=None) -> list:
    """
    Download a list of YouTube URLs via yt-dlp.
    Returns list of result dicts.
    """
    ytdlp = [sys.executable, '-m', 'yt_dlp']
    os.makedirs(output_dir, exist_ok=True)
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
            result = _download_yt_single(
                url, quality, output_dir, audio_only, ytdlp, progress_cb, pct_base, total
            )
            results.append(result)
            if progress_cb:
                progress_cb(f'  ✓ Done: {result.get("title", url)}', int(idx / total * 100))
        except Exception as exc:
            if progress_cb:
                progress_cb(f'  ✗ Error: {exc}')
            results.append({'url': url, 'status': 'error', 'error': str(exc)})

    return results


def _download_yt_single(url, quality, output_dir, audio_only,
                        ytdlp, progress_cb, pct_base, total):
    """Download one YouTube URL."""

    # Get metadata first
    info_cmd = ytdlp + ['--rm-cache-dir', '--print-json', '--no-download', '--no-playlist', url]
    raw = subprocess.check_output(info_cmd, stderr=subprocess.DEVNULL, text=True, timeout=60)
    info = json.loads(raw.splitlines()[0])

    title   = info.get('title', 'unknown')
    vid_id  = info.get('id', 'unknown')
    channel = re.sub(r'[^\w.-]', '_', info.get('channel', info.get('uploader', 'youtube')))
    duration = info.get('duration', 0)

    channel_dir = os.path.join(output_dir, channel)
    os.makedirs(channel_dir, exist_ok=True)
    out_template = os.path.join(channel_dir, f'{vid_id}.%(ext)s')

    if audio_only:
        dl_cmd = ytdlp + [
            '--no-playlist', '--rm-cache-dir',
            '-x', '--audio-format', 'mp3',
            '--audio-quality', '0',
            '--output', out_template,
            url
        ]
    else:
        if quality == 'best':
            fmt = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
        else:
            fmt = (f'bestvideo[height<={quality}][ext=mp4]+'
                   f'bestaudio[ext=m4a]/best[height<={quality}][ext=mp4]/best')
        dl_cmd = ytdlp + [
            '--no-playlist', '--rm-cache-dir',
            '--format', fmt,
            '--output', out_template,
            '--write-thumbnail', '--convert-thumbnails', 'jpg',
            '--merge-output-format', 'mp4',
            url
        ]

    proc = subprocess.Popen(
        dl_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1
    )
    for line in proc.stdout:
        line = line.rstrip()
        if progress_cb and line:
            m = re.search(r'\[download\]\s+([\d.]+)%', line)
            pct = None
            if m:
                pct = pct_base + int(float(m.group(1)) / total)
            progress_cb(line, pct)
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f'yt-dlp exited {proc.returncode}')

    # Find output file
    out_file = None
    ext = 'mp3' if audio_only else 'mp4'
    for fname in os.listdir(channel_dir):
        if fname.startswith(vid_id) and fname.endswith(f'.{ext}'):
            out_file = os.path.join(channel_dir, fname)
            break

    # Thumbnail
    thumb = None
    for fname in os.listdir(channel_dir):
        if fname.startswith(vid_id) and fname.endswith('.jpg'):
            thumb = os.path.join(channel_dir, fname)
            break

    return {
        'url':       url,
        'id':        vid_id,
        'title':     title,
        'channel':   channel,
        'duration':  duration,
        'file_path': out_file,
        'thumbnail': thumb,
        'status':    'ok',
        'audio_only': audio_only,
    }
