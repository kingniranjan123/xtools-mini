"""
Audio Tools module — FFmpeg-based
a. extract_mp3()  — extract audio from video → MP3
b. merge_audio_video() — merge MP3 audio with a video file
"""
import os, subprocess, shutil

# ── MP3 Extraction ────────────────────────────────────────────

def extract_mp3(video_paths: list, output_dir: str,
                bitrate: str = '192k', progress_cb=None) -> list:
    """
    Extract MP3 audio from a list of video files.
    Returns list of { source, output, status, error }
    """
    os.makedirs(output_dir, exist_ok=True)
    results = []
    total   = len(video_paths)

    for idx, vpath in enumerate(video_paths, 1):
        vpath = vpath.strip()
        if not vpath or not os.path.isfile(vpath):
            results.append({'source': vpath, 'status': 'error', 'error': 'File not found'})
            continue

        base     = os.path.splitext(os.path.basename(vpath))[0]
        out_path = os.path.join(output_dir, f'{base}.mp3')
        pct      = int(idx / total * 100)

        if progress_cb:
            progress_cb(f'[{idx}/{total}] Extracting audio: {os.path.basename(vpath)}', pct - 5)

        try:
            cmd = [
                'ffmpeg', '-y',
                '-i', vpath,
                '-vn',                          # no video
                '-acodec', 'libmp3lame',
                '-ab', bitrate,
                '-ar', '44100',
                out_path
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.split('\n')[-3])

            results.append({'source': vpath, 'output': out_path, 'status': 'ok'})
            if progress_cb:
                progress_cb(f'  ✓ Saved: {os.path.basename(out_path)}', pct)

        except Exception as exc:
            results.append({'source': vpath, 'output': None, 'status': 'error', 'error': str(exc)})
            if progress_cb:
                progress_cb(f'  ✗ Failed: {os.path.basename(vpath)} — {exc}')

    return results


def extract_mp3_from_folder(folder: str, bitrate: str = '192k',
                             progress_cb=None) -> list:
    """Extract MP3 from all video files inside a folder."""
    exts   = ('.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v')
    videos = [os.path.join(folder, f) for f in os.listdir(folder)
              if f.lower().endswith(exts)]
    out_dir = os.path.join(folder, 'mp3_output')
    return extract_mp3(videos, out_dir, bitrate, progress_cb)


# ── Audio-Video Merge ─────────────────────────────────────────

def merge_audio_video(video_path: str, audio_path: str,
                      output_path: str = None,
                      mode: str = 'replace',
                      progress_cb=None) -> dict:
    """
    Merge an MP3 audio file with a video file.
    mode:
      'replace' — replace video audio entirely with MP3
      'mix'     — mix existing video audio + MP3 together
    Returns { output, status, error }
    """
    if not os.path.isfile(video_path):
        return {'status': 'error', 'error': f'Video not found: {video_path}'}
    if not os.path.isfile(audio_path):
        return {'status': 'error', 'error': f'Audio not found: {audio_path}'}

    if output_path is None:
        base       = os.path.splitext(video_path)[0]
        output_path = f'{base}_merged.mp4'

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    if progress_cb:
        progress_cb(f'Merging: {os.path.basename(video_path)} + {os.path.basename(audio_path)}')

    try:
        if mode == 'replace':
            # Replace video's audio with MP3 — shortest stream determines length
            cmd = [
                'ffmpeg', '-y',
                '-i', video_path,
                '-i', audio_path,
                '-map', '0:v:0',        # video from input 0
                '-map', '1:a:0',        # audio from input 1 (mp3)
                '-c:v', 'copy',         # copy video stream (fast)
                '-c:a', 'aac',          # encode audio to AAC (MP4 compatible)
                '-shortest',            # trim to shortest stream
                output_path
            ]
        else:  # mix
            cmd = [
                'ffmpeg', '-y',
                '-i', video_path,
                '-i', audio_path,
                '-filter_complex',
                '[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=2[aout]',
                '-map', '0:v',
                '-map', '[aout]',
                '-c:v', 'copy',
                '-c:a', 'aac',
                output_path
            ]

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.split('\n')[-3])

        if progress_cb:
            progress_cb(f'  ✓ Saved: {os.path.basename(output_path)}', 100)

        return {'output': output_path, 'status': 'ok'}

    except Exception as exc:
        if progress_cb:
            progress_cb(f'  ✗ Failed: {exc}')
        return {'output': None, 'status': 'error', 'error': str(exc)}


def batch_merge(video_dir: str, audio_dir: str, output_dir: str,
                mode: str = 'replace', progress_cb=None) -> list:
    """
    Pair video files with audio files by matching base filename.
    E.g. clip01.mp4 + clip01.mp3 → clip01_merged.mp4
    """
    os.makedirs(output_dir, exist_ok=True)
    video_exts = ('.mp4', '.mov', '.avi', '.mkv', '.webm')
    videos = {os.path.splitext(f)[0]: os.path.join(video_dir, f)
              for f in os.listdir(video_dir) if f.lower().endswith(video_exts)}
    audios = {os.path.splitext(f)[0]: os.path.join(audio_dir, f)
              for f in os.listdir(audio_dir) if f.lower().endswith(('.mp3', '.aac', '.wav', '.m4a'))}

    results = []
    pairs   = [(vname, videos[vname], audios[vname])
               for vname in videos if vname in audios]
    total   = len(pairs)

    if total == 0:
        if progress_cb: progress_cb('No matching video/audio pairs found.')
        return []

    for idx, (name, vpath, apath) in enumerate(pairs, 1):
        if progress_cb:
            progress_cb(f'[{idx}/{total}] {name}', int(idx / total * 100))
        out = os.path.join(output_dir, f'{name}_merged.mp4')
        result = merge_audio_video(vpath, apath, out, mode, progress_cb)
        result['name'] = name
        results.append(result)

    return results
