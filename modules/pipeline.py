"""
Post-Download Processing Pipeline
Chains: Watermark -> Equal Split -> Part Number Overlay
All steps are optional based on flags passed in.
"""
import os
import subprocess
import glob


# ── Part number text overlay ──────────────────────────────────

def _find_font(name: str = 'Calibri') -> str:
    """Search Windows font folder for a matching .ttf file."""
    win_fonts = r'C:\Windows\Fonts'
    if os.path.isdir(win_fonts):
        for f in os.listdir(win_fonts):
            if name.lower() in f.lower() and f.lower().endswith('.ttf'):
                return os.path.join(win_fonts, f)
    return ''


def _stamp_part_number(src: str, part_num: int, dst: str) -> bool:
    """
    Overlay 'PART - N' text at top-center of the video.
    Returns True on success.
    """
    font_path = _find_font('Calibri')
    text = f'PART - {part_num}'
    safe_text = text.replace("'", r"\'").replace(':', r'\:')

    if font_path:
        vf = (
            f"drawtext=fontfile='{font_path}':"
            f"text='{safe_text}':"
            f"fontsize=40:"
            f"fontcolor=white@0.9:"
            f"x=(w-tw)/2:y=30:"
            f"shadowx=2:shadowy=2:shadowcolor=black@0.7"
        )
    else:
        vf = (
            f"drawtext=font='Calibri':"
            f"text='{safe_text}':"
            f"fontsize=40:"
            f"fontcolor=white@0.9:"
            f"x=(w-tw)/2:y=30:"
            f"shadowx=2:shadowy=2:shadowcolor=black@0.7"
        )

    cmd = ['ffmpeg', '-y', '-i', src, '-vf', vf,
           '-c:v', 'libx264', '-crf', '22', '-preset', 'fast',
           '-c:a', 'copy', dst]
    result = subprocess.run(cmd, capture_output=True)
    return result.returncode == 0


# ── Single-file watermark helper ──────────────────────────────

def _apply_text_watermark_single(src: str, dst: str, text: str,
                                  font: str, fontsize: int,
                                  color: str, opacity: float,
                                  position: str) -> bool:
    """Run ffmpeg text watermark on a single file."""
    from modules.watermarker import POSITION_MAP, _find_font as wm_find_font
    font_path = wm_find_font(font)
    pos_expr = POSITION_MAP.get(position, POSITION_MAP['BR'])
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
        vf = (
            f"drawtext=font='{font}':"
            f"text='{safe_text}':"
            f"fontsize={fontsize}:"
            f"fontcolor={color}@{opacity:.2f}:"
            f"{pos_expr}:"
            f"shadowx=1:shadowy=1:shadowcolor=black@0.6"
        )

    cmd = ['ffmpeg', '-y', '-i', src, '-vf', vf,
           '-c:v', 'libx264', '-crf', '22', '-preset', 'fast',
           '-c:a', 'copy', dst]
    result = subprocess.run(cmd, capture_output=True)
    return result.returncode == 0


# ── Main pipeline ──────────────────────────────────────────────

def run_pipeline(file_path: str, settings: dict,
                 opt_watermark: bool = False,
                 opt_split: bool = False,
                 opt_parts: bool = False,
                 progress_cb=None) -> list:
    """
    Execute the processing pipeline on a single downloaded video file.

    Steps (all optional):
      1. Watermark the video using settings from DB
      2. Split into equal-duration segments
      3. Stamp PART - N on each segment at top-center

    Returns list of final output file paths.
    """
    if not (opt_watermark or opt_split or opt_parts):
        return [file_path]  # Nothing to do

    if not os.path.isfile(file_path):
        if progress_cb:
            progress_cb(f'Pipeline: File not found — {file_path}')
        return []

    base_dir = os.path.dirname(file_path)
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    pipeline_dir = os.path.join(base_dir, f'{base_name}_processed')
    os.makedirs(pipeline_dir, exist_ok=True)

    current_file = file_path

    # ── Step 1: Watermark ─────────────────────────────────────
    if opt_watermark:
        if progress_cb:
            progress_cb('Pipeline: Applying watermark…')

        wm_text     = settings.get('wm_text', '@nikethan')
        wm_font     = settings.get('wm_font', 'Calibri')
        wm_fontsize = int(settings.get('wm_fontsize', 24))
        wm_color    = settings.get('wm_color', 'white')
        wm_opacity  = float(settings.get('wm_opacity', 0.85))
        wm_position = settings.get('wm_position', 'BR')

        wm_out = os.path.join(pipeline_dir, f'{base_name}_wm.mp4')
        ok = _apply_text_watermark_single(
            src=current_file, dst=wm_out,
            text=wm_text, font=wm_font, fontsize=wm_fontsize,
            color=wm_color, opacity=wm_opacity, position=wm_position
        )
        if ok:
            current_file = wm_out
            if progress_cb:
                progress_cb('Pipeline: ✓ Watermark applied')
        else:
            if progress_cb:
                progress_cb('Pipeline: ✗ Watermark failed — continuing without it')

    # ── Step 2: Split into equal parts ───────────────────────
    split_files = [current_file]  # fallback: just one unsplit file
    if opt_split:
        if progress_cb:
            progress_cb('Pipeline: Splitting into equal segments…')

        split_secs = int(settings.get('split_duration', 60))
        use_cuda   = settings.get('cuda_enabled') == '1'
        split_dir  = os.path.join(pipeline_dir, 'parts')
        os.makedirs(split_dir, exist_ok=True)

        from modules.splitter import split_equal
        try:
            split_files = split_equal(
                input_path=current_file,
                n=split_secs,
                out_dir=split_dir,
                use_cuda=use_cuda,
                progress_cb=progress_cb
            )
            if progress_cb:
                progress_cb(f'Pipeline: ✓ Split into {len(split_files)} parts')
        except Exception as e:
            if progress_cb:
                progress_cb(f'Pipeline: ✗ Split failed — {e}')
            split_files = [current_file]

    # ── Step 3: Stamp PART - N on each segment ───────────────
    final_files = []
    if opt_parts:
        if progress_cb:
            progress_cb(f'Pipeline: Stamping part numbers on {len(split_files)} file(s)…')

        for idx, part_file in enumerate(split_files, 1):
            out_name = f'part_{idx:03d}_stamped.mp4'
            out_path = os.path.join(os.path.dirname(part_file), out_name)
            ok = _stamp_part_number(part_file, idx, out_path)
            if ok:
                final_files.append(out_path)
                if progress_cb:
                    progress_cb(f'Pipeline: ✓ Part {idx} stamped → {out_name}')
            else:
                # Fall back to unstamped
                final_files.append(part_file)
                if progress_cb:
                    progress_cb(f'Pipeline: ✗ Stamp failed for part {idx} — using original')
    else:
        final_files = split_files

    if progress_cb:
        progress_cb(f'Pipeline: ✓ Complete — {len(final_files)} file(s) ready', 100)

    return final_files
