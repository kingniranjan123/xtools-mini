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


# ── Cross-platform font resolver ─────────────────────────────────

_FONT_CANDIDATES = [
    # Windows
    'C:/Windows/Fonts/arial.ttf',
    'C:/Windows/Fonts/Arial.ttf',
    'C:/Windows/Fonts/arialbd.ttf',
    'C:/Windows/Fonts/calibri.ttf',
    'C:/Windows/Fonts/tahoma.ttf',
    'C:/Windows/Fonts/segoeui.ttf',
    # Linux / Mac
    '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    '/Library/Fonts/Arial.ttf',
    '/System/Library/Fonts/Helvetica.ttf',
]

# Bundled fallback (we'll copy a font into static/ if all system paths fail)
_BUNDLED_FONT = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                              'static', 'NotoSans-Bold.ttf')

def _resolve_font() -> str:
    """Return the first available font file path, or empty string if none found."""
    for path in _FONT_CANDIDATES:
        if os.path.isfile(path):
            return path.replace('\\', '/').replace(':', '\\:')  # ffmpeg escape
    if os.path.isfile(_BUNDLED_FONT):
        return _BUNDLED_FONT.replace('\\', '/').replace(':', '\\:')
    return ''


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



def _wrap_title(text: str, canvas_w: int, font_size: int, max_lines: int = 4) -> list:
    """
    Split text into lines that fit within canvas_w pixels.
    Returns list of line strings (max max_lines lines, last truncated with '…' if needed).
    Approximation: mono font char ≈ font_size * 0.55 wide.
    """
    import textwrap
    chars_per_line = max(10, int(canvas_w * 0.90 / (font_size * 0.52)))
    lines = textwrap.wrap(text, width=chars_per_line)
    if not lines:
        return [text]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        if len(lines[-1]) > chars_per_line - 3:
            lines[-1] = lines[-1][:chars_per_line - 3].rstrip() + '…'
        else:
            lines[-1] = lines[-1] + '…'
    return lines


def build_filters(in_w: int, in_h: int, part_num: int, title: str, watermark: str,
                  show_title: bool, show_part_label: bool, show_watermark: bool,
                  title_pos_pct: float = 20.0, part_pos_pct: float = 82.0,
                  output_size: str = 'instagram') -> str:
    """
    Build the -vf filter chain for one part.
    output_size: 'instagram' (9:16 1080×1920 letterbox) | 'original' (keep source dims)
    """
    is_portrait = in_h > in_w

    if output_size == 'original':
        # Skip scale/pad — output same dimensions as input; set canvas for text
        canvas_w = in_w
        canvas_h = in_h
        filters  = []
    else:
        # Instagram 9:16 letterbox
        if is_portrait:
            scale_pad = (
                f"scale={REEL_W}:{REEL_H}:force_original_aspect_ratio=decrease,"
                f"pad={REEL_W}:{REEL_H}:(ow-iw)/2:(oh-ih)/2:black"
            )
        else:
            scale_pad = (
                f"scale={REEL_W}:-2,"
                f"pad={REEL_W}:{REEL_H}:(ow-iw)/2:(oh-ih)/2:black"
            )
        canvas_w = REEL_W
        canvas_h = REEL_H
        filters  = [scale_pad]

    font_file = _resolve_font()
    font_attr = f"fontfile='{font_file}':" if font_file else ''

    # Title: word-wrapped, centered placement
    if show_title and title.strip():
        full_title = f"{title.strip()} - Part {part_num}"
        title_font_size = 48
        min_font_size = 32
        max_lines = 4
        lines = _wrap_title(full_title, canvas_w, title_font_size, max_lines=max_lines)
        while lines and lines[-1].endswith('…') and title_font_size > min_font_size:
            title_font_size -= 2
            lines = _wrap_title(full_title, canvas_w, title_font_size, max_lines=max_lines)
        line_height = int(title_font_size * 1.35)
        base_y_px = int(canvas_h * title_pos_pct / 100.0)

        for i, line in enumerate(lines):
            safe_line = (line
                         .replace("'", "\\'")
                         .replace(":", "\\:")
                         .replace(",", "\\,"))
            y_px = base_y_px + i * line_height
            filters.append(
                f"drawtext=text='{safe_line}':"
                f"{font_attr}"
                f"fontcolor=white:fontsize={title_font_size}:"
                f"x=(w-text_w)/2:y={y_px}:"
                f"shadowcolor=black@0.85:shadowx=2:shadowy=2"
            )

    # Part label: custom centered bottom placement
    if show_part_label:
        part_label = f"Part -{part_num}"
        default_y_expr = f"h*{part_pos_pct/100.0:.2f}"
        y_pos = default_y_expr
        if output_size == 'instagram':
            # Avoid comma-based expressions (e.g. max(a,b)) that can break parsing
            # when this filter chain is embedded inside filter_complex.
            y_pos = "h-text_h-54"
        filters.append(
            f"drawtext=text='{part_label}':"
            f"{font_attr}"
            f"fontcolor=white:fontsize=64:"
            f"x=(w-text_w)/2:y={y_pos}:"
            f"shadowcolor=black@0.9:shadowx=3:shadowy=3"
        )

    # Watermark: bottom-right, semi-transparent
    if show_watermark and watermark.strip():
        safe_wm = (watermark
                   .replace("'", "\\'")
                   .replace(":", "\\:")
                   .replace(",", "\\,"))
        filters.append(
            f"drawtext=text='{safe_wm}':"
            f"{font_attr}"
            f"fontcolor=white@0.55:fontsize=34:"
            f"x=w-text_w-28:y=h-text_h-28:"
            f"shadowcolor=black@0.5:shadowx=1:shadowy=1"
        )

    # If no filters at all, pass through unchanged
    return ','.join(filters) if filters else 'copy'


# ── Single-part encode ────────────────────────────────────────────

def encode_part(input_path: str, output_path: str,
                start_sec: float, duration_sec: float,
                vf: str, overlay_image_path: str = '', overlay_image_zoom: float = 1.0,
                overlay_image_comp_pct: float = 100.0,
                bottom_compartment_px: int = 0,
                output_size: str = 'instagram',
                progress_cb=None) -> bool:
    overlay = (overlay_image_path or '').strip()
    comp_pct = max(1.0, min(100.0, float(overlay_image_comp_pct or 100.0)))
    zoom = max(1.0, min(2.0, float(overlay_image_zoom or 1.0)))
    cmd = [FFMPEG, '-y', '-ss', str(start_sec), '-t', str(duration_sec), '-i', input_path]
    if overlay and os.path.isfile(overlay):
        base_expr = vf if vf and vf != 'copy' else 'null'
        base_bottom_px = max(2, int(bottom_compartment_px or 0))
        if output_size == 'instagram' and base_bottom_px <= 0:
            base_bottom_px = int(REEL_H / 3.0)
        if output_size != 'instagram' and base_bottom_px <= 0:
            base_bottom_px = int(REEL_H * 0.16)
        if output_size == 'instagram':
            container_w = REEL_W
            container_h = max(2, int(base_bottom_px * (comp_pct / 100.0)))
            overlay_y = f"'H-{base_bottom_px}+(({base_bottom_px}-h)/2)'"
        else:
            # Keep old behavior for "original" outputs, but avoid hard-coding side margins.
            # This makes overlay sizing follow the input/output frame width instead of 1080.
            probe = probe_video(input_path)
            container_w = max(2, int(probe.get('width', REEL_W) * 0.92))
            container_h = max(2, int(base_bottom_px * (comp_pct / 100.0)))
            overlay_y = "'H-h-40'"
        cmd += [
            '-loop', '1', '-i', overlay,
            '-filter_complex',
            (
                f"[0:v]{base_expr}[base];"
                f"[1:v]format=rgba,"
                f"scale=w={container_w}:h={container_h}:force_original_aspect_ratio=decrease,"
                f"scale=w=iw*{zoom:.4f}:h=ih*{zoom:.4f}[ovzoom];"
                f"[ovzoom]crop=w='min(iw,{container_w})':h='min(ih,{container_h})':"
                f"x='(iw-ow)/2':y='(ih-oh)/2'[ov];"
                f"[base][ov]overlay=x='(W-w)/2':y={overlay_y}:shortest=1[vout]"
            ),
            '-map', '[vout]', '-map', '0:a?'
        ]
    else:
        cmd += ['-vf', vf]
    cmd += [
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
    output_size: str  = 'instagram',   # 'instagram' | 'original'
    overlay_image_path: str = '',
    overlay_image_zoom: float = 1.0,
    overlay_image_comp_pct: float = 100.0,
    # Optional: clip a specific range before splitting
    clip_start_sec: float = 0.0,
    clip_end_sec:   float = 0.0,   # 0 = full video
    progress_cb=None,
) -> dict:
    """
    Convert an MP4 video into Instagram Reel parts.
    output_size: 'instagram' (9:16 letterbox) | 'original' (keep source dims)
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
    if output_size == 'instagram':
        # Fixed lower-third compartment for image overlays in 9:16 output.
        bottom_compartment_px = int(REEL_H / 3.0)
    else:
        bottom_compartment_px = max(0, int(in_h * 0.18))

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
                           title_pos_pct, part_pos_pct, output_size=output_size)

        if progress_cb:
            progress_cb(f'🎬 Encoding Part {part_num}/{num_parts} → {out_name}')

        ok = encode_part(
            input_path, out_path, seg_start, seg_dur, vf,
            overlay_image_path=overlay_image_path,
            overlay_image_zoom=overlay_image_zoom,
            overlay_image_comp_pct=overlay_image_comp_pct,
            bottom_compartment_px=bottom_compartment_px,
            output_size=output_size,
            progress_cb=progress_cb
        )
        if ok:
            parts.append(out_path)
            if progress_cb:
                progress_cb(f'  ✅ Part {part_num} done')
        else:
            errors.append(f'Part {part_num} failed')
            if progress_cb:
                progress_cb(f'  ❌ Part {part_num} failed — check ffmpeg logs')

    return {'parts': parts, 'errors': errors}
