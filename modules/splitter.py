"""
CUDA-accelerated video splitter.
  split_equal()  — equal N-second segments
  split_trailer() — time-range clip extraction (optionally concatenated)
"""
import os, subprocess, math, glob


# ── helpers ──────────────────────────────────────────────────

def _get_duration(path: str) -> float:
    """Use ffprobe to get video duration in seconds."""
    cmd = [
        'ffprobe', '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        path
    ]
    out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True, encoding='utf-8', errors='replace').strip()
    return float(out) if out else 0.0


def _codec_args(use_cuda: bool) -> list:
    if use_cuda:
        return ['-c:v', 'h264_nvenc', '-rc', 'vbr', '-cq', '24']
    return ['-c:v', 'libx264', '-crf', '23', '-preset', 'fast']


def _hwaccel_args(use_cuda: bool) -> list:
    return ['-hwaccel', 'cuda'] if use_cuda else []


# ── Equal split ───────────────────────────────────────────────

def split_equal(input_path: str, n: int, out_dir: str,
                use_cuda: bool = False, output_format: str = 'original',
                progress_cb=None) -> list:
    """
    Split input_path into segments of exactly n seconds.
    output_format: 'original' (keep source dims) | 'instagram' (letterbox to 1080x1920)
    Returns list of output file paths.
    """
    os.makedirs(out_dir, exist_ok=True)
    duration  = _get_duration(input_path)
    n_segments = math.ceil(duration / n) if duration > 0 else 1

    if progress_cb:
        progress_cb(f'Duration: {duration:.1f}s → {n_segments} segments of {n}s', 0)
        progress_cb(f'CUDA: {"enabled (h264_nvenc)" if use_cuda else "disabled (libx264)"}')

    out_pattern = os.path.join(out_dir, 'part_%03d.mp4')

    cmd = (
        _hwaccel_args(use_cuda)
        + ['-i', input_path]
        + _codec_args(use_cuda)
        + ['-c:a', 'aac']
        + [
            '-segment_time', str(n),
            '-f', 'segment',
            '-reset_timestamps', '1',
            out_pattern
        ]
    )
    cmd = ['ffmpeg', '-y'] + cmd

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding='utf-8', errors='replace', bufsize=1
    )
    for line in proc.stdout:
        line = line.rstrip()
        if progress_cb and line:
            import re
            m = re.search(r'part_(\d+)\.mp4', line)
            if m:
                seg = int(m.group(1))
                pct = int(seg / max(n_segments, 1) * (80 if output_format == 'instagram' else 95))
                progress_cb(line, pct)
            else:
                progress_cb(line)

    proc.wait()
    if proc.returncode not in (0, 1):
        raise RuntimeError(f'ffmpeg exited with code {proc.returncode}')

    files = sorted(glob.glob(os.path.join(out_dir, 'part_*.mp4')))

    # Instagram 9:16 re-encode: letterbox each segment into 1080×1920 (no crop)
    if output_format == 'instagram' and files:
        if progress_cb:
            progress_cb('Re-encoding to Instagram 9:16 letterbox format…', 80)
        ig_dir = os.path.join(out_dir, 'instagram')
        os.makedirs(ig_dir, exist_ok=True)
        converted = []
        total = len(files)
        for i, seg_path in enumerate(files):
            base   = os.path.basename(seg_path)
            ig_out = os.path.join(ig_dir, base)
            vf = (
                "scale=1080:1920:force_original_aspect_ratio=decrease,"
                "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,"
                "setsar=1"
            )
            ig_cmd = [
                'ffmpeg', '-y',
                '-i', seg_path,
                '-vf', vf,
            ] + _codec_args(use_cuda) + [
                '-c:a', 'aac', '-b:a', '192k',
                ig_out
            ]
            sub = subprocess.Popen(
                ig_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace', bufsize=1
            )
            sub.communicate()
            if sub.returncode == 0:
                converted.append(ig_out)
                if progress_cb:
                    pct = 80 + int((i + 1) / total * 18)
                    progress_cb(f'Instagram format: {base}', pct)
        files = converted if converted else files

    if progress_cb:
        progress_cb(f'✓ Created {len(files)} segments', 100)
    return files


# ── Trailer (time-range) extractor ───────────────────────────

def split_trailer(input_path: str, clips: list, out_dir: str,
                  concat: bool = False, use_cuda: bool = False,
                  output_format: str = 'original',
                  progress_cb=None) -> list:
    """
    Extract multiple from/to clips.
    clips: list of {from, to, label}
    Returns list of {path, label} dicts.
    """
    os.makedirs(out_dir, exist_ok=True)
    results  = []
    total    = len(clips)

    for idx, clip in enumerate(clips, 1):
        start = clip.get('from', '00:00:00')
        end   = clip.get('to',   '00:00:30')
        label = clip.get('label') or f'clip_{idx:03d}'

        safe_label = ''.join(c if c.isalnum() or c in '-_' else '_' for c in label)
        out_path   = os.path.join(out_dir, f'{idx:03d}_{safe_label}.mp4')

        pct = int((idx - 1) / total * 90)
        if progress_cb:
            progress_cb(f'[{idx}/{total}] Extracting: {start} → {end}  ({label})', pct)

        cmd = ['ffmpeg', '-y'] + _hwaccel_args(use_cuda) + [
            '-ss', start, '-to', end,
            '-i', input_path
        ] + ([
            '-vf', 'scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1'
        ] if output_format == 'instagram' else []) + _codec_args(use_cuda) + ['-c:a', 'aac', out_path]

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace', bufsize=1
        )
        for line in proc.stdout:
            if progress_cb and line.strip():
                progress_cb(line.rstrip())
        proc.wait()
        if proc.returncode not in (0, 1):
            if progress_cb:
                progress_cb(f'  ✗ Failed: clip {idx}')
            continue

        results.append({'path': out_path, 'label': label})
        if progress_cb:
            progress_cb(f'  ✓ Saved: {os.path.basename(out_path)}', int(idx / total * 90))

    # Concatenate if requested
    if concat and len(results) > 1:
        if progress_cb:
            progress_cb('Concatenating clips…', 90)
        concat_path = _concatenate(results, out_dir, use_cuda)
        if progress_cb:
            progress_cb(f'✓ Trailer: {os.path.basename(concat_path)}', 100)
        results = [{'path': concat_path, 'label': 'trailer'}]
    elif progress_cb:
        progress_cb(f'✓ Done — {len(results)} clip(s) extracted', 100)

    return results


def _concatenate(clips: list, out_dir: str, use_cuda: bool) -> str:
    """Concatenate clip files using ffmpeg concat demuxer."""
    filelist_path = os.path.join(out_dir, '_filelist.txt')
    with open(filelist_path, 'w', encoding='utf-8') as f:
        for c in clips:
            safe = c['path'].replace("'", r"'\''")
            f.write(f"file '{safe}'\n")

    out_path = os.path.join(out_dir, 'trailer.mp4')
    cmd = [
        'ffmpeg', '-y',
        '-f', 'concat', '-safe', '0',
        '-i', filelist_path,
        '-c', 'copy',
        out_path
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    os.remove(filelist_path)
    return out_path
