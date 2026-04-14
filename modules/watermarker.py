"""
FFmpeg-based watermark module.
Supports:
  - Text watermark: Calibri 24pt, bottom-right (default)
  - Image watermark: overlay with position, opacity, scale
"""
import os, re, subprocess, shutil, uuid

POSITION_MAP = {
    'TL': 'x=20:y=20',
    'TC': 'x=(w-tw)/2:y=20',
    'TR': 'x=w-tw-20:y=20',
    'ML': 'x=20:y=(h-th)/2',
    'C' : 'x=(w-tw)/2:y=(h-th)/2',
    'MR': 'x=w-tw-20:y=(h-th)/2',
    'BL': 'x=20:y=h-th-20',
    'BC': 'x=(w-tw)/2:y=h-th-20',
    'BR': 'x=w-tw-20:y=h-th-20',
}

# Overlay POSITION_MAP (for image watermarks)
OVERLAY_MAP = {
    'TL': 'x=10:y=10',
    'TC': 'x=(W-w)/2:y=10',
    'TR': 'x=W-w-10:y=10',
    'ML': 'x=10:y=(H-h)/2',
    'C' : 'x=(W-w)/2:y=(H-h)/2',
    'MR': 'x=W-w-10:y=(H-h)/2',
    'BL': 'x=10:y=H-h-10',
    'BC': 'x=(W-w)/2:y=H-h-10',
    'BR': 'x=W-w-10:y=H-h-10',
}

# Default text watermark settings (Calibri 24pt BR as specified)
DEFAULT_TEXT_WM = {
    'font':     'Calibri',
    'fontsize': 24,
    'color':    'white',
    'opacity':  0.85,
    'position': 'BR',
}


def apply_watermark_to_folder(folder, watermark_path=None, position='BR',
                               opacity=0.75, scale=0.15,
                               output_mode='new_folder', progress_cb=None,
                               # Text watermark params
                               wm_type='text',
                               wm_text='@nikethan',
                               wm_font='Calibri',
                               wm_fontsize=24,
                               wm_color='white',
                               wm_text_opacity=0.85):
    """
    Apply watermark to all .mp4 files in `folder`.
    wm_type: 'text' (default) or 'image'
    Returns count of files processed.
    """
    mp4_files = [f for f in os.listdir(folder) if f.lower().endswith('.mp4')]
    total     = len(mp4_files)
    count     = 0

    if output_mode == 'new_folder':
        out_dir = os.path.join(folder, 'watermarked')
        os.makedirs(out_dir, exist_ok=True)
    else:
        out_dir = folder

    for idx, fname in enumerate(mp4_files, 1):
        src = os.path.join(folder, fname)
        dst = os.path.join(out_dir, fname) if output_mode == 'new_folder' else os.path.join(folder, '__wm__' + fname)

        pct = int(idx / total * 100)
        if progress_cb:
            progress_cb(f'[{idx}/{total}] Watermarking: {fname}', pct)

        try:
            if wm_type == 'text':
                _ffmpeg_text_watermark(src, dst, wm_text, wm_font, wm_fontsize,
                                       wm_color, wm_text_opacity, position)
            else:
                if not watermark_path or not os.path.isfile(watermark_path):
                    if progress_cb: progress_cb(f'  ✗ No watermark image for {fname}')
                    continue
                pos_expr = OVERLAY_MAP.get(position, OVERLAY_MAP['BR'])
                _ffmpeg_image_watermark(src, dst, watermark_path, pos_expr, opacity, scale)

            if output_mode == 'overwrite':
                os.replace(dst, src)
            count += 1
        except Exception as exc:
            if progress_cb:
                progress_cb(f'  ✗ Failed: {fname} — {exc}')

    return count


def _ffmpeg_text_watermark(src, dst, text, font, fontsize, color, opacity, position):
    """
    Overlay text using FFmpeg drawtext filter.
    Default: Calibri 24pt, white, bottom-right.
    """
    # Find the font file — FFmpeg needs a path to the .ttf/.otf
    font_path = _find_font(font)

    pos_expr = POSITION_MAP.get(position, POSITION_MAP['BR'])

    # Escape text for FFmpeg
    safe_text = text.replace("'", r"\'").replace(':', r'\:')

    if font_path:
        vf = (
            f"drawtext=fontfile='{font_path}':"
            f"text='{safe_text}':"
            f"fontsize={fontsize}:"
            f"fontcolor={color}@{opacity:.2f}:"
            f"{pos_expr}:"
            f"shadowx=1:shadowy=1:shadowcolor=black@0.6"
        )
    else:
        # Fallback: let FFmpeg use system font by name
        vf = (
            f"drawtext=font='{font}':"
            f"text='{safe_text}':"
            f"fontsize={fontsize}:"
            f"fontcolor={color}@{opacity:.2f}:"
            f"{pos_expr}:"
            f"shadowx=1:shadowy=1:shadowcolor=black@0.6"
        )

    cmd = ['ffmpeg', '-y', '-i', src, '-vf', vf, '-codec:a', 'copy', dst]
    subprocess.run(cmd, check=True, capture_output=True)


def _find_font(font_name: str) -> str:
    """
    Try to locate a system font file matching font_name.
    Returns path string or empty string if not found.
    """
    import platform
    system = platform.system()
    search_dirs = []

    if system == 'Windows':
        windir = os.environ.get('WINDIR', 'C:\\Windows')
        search_dirs = [
            os.path.join(windir, 'Fonts'),
            os.path.expanduser('~\\AppData\\Local\\Microsoft\\Windows\\Fonts'),
        ]
    elif system == 'Darwin':  # macOS
        search_dirs = ['/Library/Fonts', '/System/Library/Fonts', os.path.expanduser('~/Library/Fonts')]
    else:  # Linux
        search_dirs = ['/usr/share/fonts', '/usr/local/share/fonts', os.path.expanduser('~/.fonts')]

    name_lower = font_name.lower().replace(' ', '')
    for d in search_dirs:
        if not os.path.isdir(d):
            continue
        for root, _, files in os.walk(d):
            for f in files:
                if f.lower().endswith(('.ttf', '.otf')):
                    base = os.path.splitext(f)[0].lower().replace(' ', '').replace('-', '').replace('_', '')
                    if name_lower in base or base.startswith(name_lower[:6]):
                        return os.path.join(root, f)
    return ''


def _ffmpeg_image_watermark(src, dst, wm_path, pos_expr, opacity, scale):
    """Apply image overlay via FFmpeg filter_complex."""
    vf = (
        f"[1:v]scale=iw*{scale:.4f}:-1,format=rgba,"
        f"colorchannelmixer=aa={opacity:.4f}[wm];"
        f"[0:v][wm]overlay={pos_expr}"
    )
    cmd = ['ffmpeg', '-y', '-i', src, '-i', wm_path,
           '-filter_complex', vf, '-codec:a', 'copy', dst]
    subprocess.run(cmd, check=True, capture_output=True)


def fetch_ig_watermark(instagram_url: str, wm_dir: str, cookies_file: str = None) -> dict:
    """
    Fetch profile picture from an Instagram URL using yt-dlp.
    Returns dict with watermark_path and username.
    """
    ytdlp = shutil.which('yt-dlp') or 'yt-dlp'

    try:
        cmd = [ytdlp, '--print-json', '--no-download', '--playlist-items', '1']
        if cookies_file and os.path.isfile(cookies_file):
            cmd += ['--cookies', cookies_file]
        cmd.append(instagram_url)

        import json
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True, timeout=30)
        info = json.loads(out.splitlines()[0] if out else '{}')

        username      = info.get('uploader_id') or info.get('uploader') or 'unknown'
        username      = re.sub(r'[^\w.-]', '_', username)
        thumbnail_url = info.get('thumbnail') or (info.get('thumbnails') or [{}])[-1].get('url', '')

        if not thumbnail_url:
            return {'error': 'No thumbnail found'}

        import urllib.request
        dest = os.path.join(wm_dir, f'{username}.jpg')
        urllib.request.urlretrieve(thumbnail_url, dest)

        return {'watermark_path': dest, 'username': username}
    except Exception as exc:
        return {'error': str(exc)}

