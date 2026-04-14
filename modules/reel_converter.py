"""
Reel Converter — YouTube → Instagram 9:16 Reel Splitter
========================================================
Rules:
  • Scale to 1080 width preserving aspect ratio (no cropping)
  • Pad to 1080×1920 centered (letterbox / cinemascope bars kept)
  • Title text: top-center  →  "Movie Name - Part X"
  • Part label: center of bottom black bar  →  "Part -X"
  • Watermark: bottom-right, semi-transparent
  • Split into equal parts by duration (seconds per part)
  • Optional: extract only specific time range first, then split that
  • H.264 / AAC, fast preset, CRF 23
"""

import os
import subprocess
import sys
import json
from math import ceil

FFMPEG  = 'ffmpeg'
FFPROBE = 'ffprobe'

REEL_W = 1080
REEL_H = 1920


# ── Video probe ───────────────────────────────────────────────────

def probe_video(path: str) -> dict:
    cmd = [
        FFPROBE, '-v', 'error',
        '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height,duration',
        '-of', 'json',
        path,
    ]
    try:
        raw = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
        out = raw.decode('utf-8', errors='replace')
        data = json.loads(out)
        s = data['streams'][0]
        return {
            'width':    int(s.get('width', 0)),
            'height':   int(s.get('height', 0)),
            'duration': float(s.get('duration', 0) or 0),
        }
    except Exception as e:
        raise RuntimeError(f'ffprobe failed: {e}')



# ── FFmpeg filter builder ─────────────────────────────────────────

def build_filters(in_w: int, in_h: int, part_num: int, title: str, watermark: str,
                  show_title: bool, show_part_label: bool, show_watermark: bool,
                  title_pos_pct: float = 20.0, part_pos_pct: float = 82.0) -> str:
    """
    Build the -vf filter chain for one part.
    """
    is_portrait = in_h > in_w

    if is_portrait:
        # Already portrait: scale to fit within 1080×1920
        scale_pad = (
            f"scale={REEL_W}:{REEL_H}:force_original_aspect_ratio=decrease,"
            f"pad={REEL_W}:{REEL_H}:(ow-iw)/2:(oh-ih)/2:black"
        )
    else:
        # Landscape / cinemascope: scale width to 1080, pad height to 1920
        scale_pad = (
            f"scale={REEL_W}:-2,"
            f"pad={REEL_W}:{REEL_H}:(ow-iw)/2:(oh-ih)/2:black"
        )

    filters = [scale_pad]

    # Title: Custom centered placement
    if show_title and title.strip():
        safe_title = title.replace("'", "\\'").replace(":", "\\:")
        text = f"{safe_title} - Part {part_num}"
        y_pos = f"h*{title_pos_pct/100.0:.2f}"
        filters.append(
            f"drawtext=text='{text}':"
            f"fontcolor=white:fontsize=52:font='Sans':"
            f"x=(w-text_w)/2:y={y_pos}:"
            f"shadowcolor=black@0.85:shadowx=2:shadowy=2"
        )

    # Part label: Custom centered bottom placement
    if show_part_label:
        part_label = f"Part -{part_num}"
        y_pos = f"h*{part_pos_pct/100.0:.2f}"
        filters.append(
            f"drawtext=text='{part_label}':"
            f"fontcolor=white:fontsize=64:font='Sans':"
            f"x=(w-text_w)/2:y={y_pos}:"
            f"shadowcolor=black@0.9:shadowx=3:shadowy=3"
        )

    # Watermark: bottom-right, semi-transparent
    if show_watermark and watermark.strip():
        safe_wm = watermark.replace("'", "\\'").replace(":", "\\:")
        filters.append(
            f"drawtext=text='{safe_wm}':"
            f"fontcolor=white@0.55:fontsize=34:font='Sans':"
            f"x=w-text_w-28:y=h-text_h-28:"
            f"shadowcolor=black@0.5:shadowx=1:shadowy=1"
        )

    return ','.join(filters)


# ── Single-part encode ────────────────────────────────────────────

def encode_part(input_path: str, output_path: str,
                start_sec: float, duration_sec: float,
                vf: str, progress_cb=None) -> bool:
    cmd = [
        FFMPEG, '-y',
        '-ss', str(start_sec),
        '-t',  str(duration_sec),
        '-i',  input_path,
        '-vf', vf,
        '-c:v', 'libx264',
        '-preset', 'fast',
        '-crf', '23',
        '-c:a', 'aac',
        '-b:a', '128k',
        '-movflags', '+faststart',
        output_path,
    ]
    if progress_cb:
        progress_cb(f'  ffmpeg start: {os.path.basename(output_path)}')

    # Use binary mode + explicit decode to avoid Windows cp1252 codec crash
    # on FFmpeg's unicode progress characters (e.g. block chars 0x8d etc.)
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        if progress_cb:
            # Safely decode stderr — ignore any undecodable bytes
            stderr_text = (result.stderr or b'').decode('utf-8', errors='replace')
            progress_cb(f'  \u2717 ffmpeg error: {stderr_text[-400:]}')
        return False
    return True



# ── Main conversion entry point ───────────────────────────────────

def convert_to_reels(
    input_path: str,
    output_dir: str,
    title: str        = '',
    watermark: str    = '',
    part_duration_sec: int = 60,
    show_title: bool  = True,
    show_part_label: bool = True,
    show_watermark: bool = True,
    title_pos_pct: float = 20.0,
    part_pos_pct: float = 82.0,
    # Optional: clip a specific range before splitting
    clip_start_sec: float = 0.0,
    clip_end_sec:   float = 0.0,   # 0 = full video
    progress_cb=None,
) -> dict:
    """
    Convert an MP4 video into Instagram Reel parts.
    Returns { parts: [path, ...], errors: [...] }
    """
    os.makedirs(output_dir, exist_ok=True)

    if progress_cb:
        progress_cb(f'📐 Probing: {os.path.basename(input_path)}')

    try:
        info = probe_video(input_path)
    except Exception as e:
        return {'parts': [], 'errors': [str(e)]}

    in_w  = info['width']
    in_h  = info['height']
    total = info['duration']

    if progress_cb:
        progress_cb(f'  Resolution: {in_w}×{in_h}, Duration: {total:.1f}s')

    # Apply optional clip range
    work_start = clip_start_sec if clip_start_sec > 0 else 0.0
    work_end   = clip_end_sec   if clip_end_sec   > 0 else total
    work_dur   = work_end - work_start
    if work_dur <= 0:
        return {'parts': [], 'errors': ['Invalid clip range']}

    num_parts = max(1, ceil(work_dur / part_duration_sec))
    if progress_cb:
        progress_cb(f'🔪 Splitting into {num_parts} parts of ~{part_duration_sec}s each')

    parts  = []
    errors = []

    base_name = os.path.splitext(os.path.basename(input_path))[0]

    for i in range(num_parts):
        part_num  = i + 1
        seg_start = work_start + (i * part_duration_sec)
        seg_dur   = min(part_duration_sec, work_end - seg_start)

        if seg_dur <= 0:
            break

        out_name = f"{base_name}_part{part_num:02d}.mp4"
        out_path = os.path.join(output_dir, out_name)

        vf = build_filters(in_w, in_h, part_num, title, watermark,
                           show_title, show_part_label, show_watermark,
                           title_pos_pct, part_pos_pct)

        if progress_cb:
            progress_cb(f'🎬 Encoding Part {part_num}/{num_parts} → {out_name}')

        ok = encode_part(input_path, out_path, seg_start, seg_dur, vf, progress_cb)
        if ok:
            parts.append(out_path)
            if progress_cb:
                progress_cb(f'  ✅ Part {part_num} done')
        else:
            errors.append(f'Part {part_num} failed')
            if progress_cb:
                progress_cb(f'  ❌ Part {part_num} failed — check ffmpeg logs')

    return {'parts': parts, 'errors': errors}
