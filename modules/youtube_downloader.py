import os, re, json, subprocess, shutil, sys, threading, time, random
from concurrent.futures import ThreadPoolExecutor, as_completed

def _clean_err(stderr_text):
    """Extract the last meaningful error line from yt-dlp stderr."""
    if not stderr_text: return "Unknown error"
    
    # Priority Errors (Specific Blocks)
    if 'cookies are no longer valid' in stderr_text.lower():
        return "❌ Cookies Expired! Please re-export from browser and upload again."
    if 'confirm you' in stderr_text.lower() and 'bot' in stderr_text.lower():
        return "❌ Bot Blocked! Follow 'Bypass Guide' (Install Deno & Re-export Cookies)."
    if 'Requested format is not available' in stderr_text:
        return "❌ Format Hidden! (Usually fixed by installing Deno & New Cookies)."

    lines = [line.strip() for line in stderr_text.splitlines() if line.strip()]
    # Look for 'ERROR:' or 'YouTube said:' lines
    for line in reversed(lines):
        if 'ERROR:' in line or 'YouTube said:' in line:
            return line
    return lines[-1] if lines else "Unknown error"

def download_youtube(urls: list, quality: str, output_dir: str,
                     audio_only: bool = False, custom_dir: bool = False, 
                     download_subs: bool = False, download_thumb: bool = False,
                     concurrency: int = 1, browser: str = None, cookie_file: str = None, 
                     request_delay: float = 0,
                     check_exists_cb=None, progress_cb=None) -> list:
    """
    Download a list of YouTube URLs via yt-dlp in parallel.
    """
    ytdlp = [
        sys.executable, '-m', 'yt_dlp', 
        '--no-check-certificates',
        '--geo-bypass',
        '--force-ipv4',
        '--js-runtime', 'node',
        '--remote-components', 'ejs:github',
        '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36'
    ]
    os.makedirs(output_dir, exist_ok=True)
    
    # 1. Expand Playlists
    expanded_urls = []
    if progress_cb:
        progress_cb("Checking for playlists...", 0)
    
    for url in urls:
        url = url.strip()
        if not url: continue
        if 'list=' in url:
            if progress_cb: progress_cb(f"Expanding playlist: {url}", None)
            try:
                cmd = ytdlp + ['--flat-playlist', '--get-url', '--no-warnings']
                if cookie_file and os.path.isfile(cookie_file):
                    cmd += ['--cookies', cookie_file]
                elif browser:
                    cmd += ['--cookies-from-browser', browser]
                cmd.append(url)
                
                raw = subprocess.check_output(cmd, text=True, encoding='utf-8', errors='replace').strip()
                p_urls = [u.strip() for u in raw.splitlines() if u.strip()]
                expanded_urls.extend(p_urls)
            except Exception as e:
                expanded_urls.append(url)
        else:
            expanded_urls.append(url)

    results = []
    total = len(expanded_urls)
    if total == 0: return []

    # Thread-safe tracking
    lock = threading.Lock()
    state = {
        'completed_count': 0
    }

    def worker(idx, url):
        
        # 2. Get Info & Deduplication
        try:
            # Add safety delay to mimic human behavior and avoid rate limits
            if request_delay > 0:
                # Randomize slightly (e.g. 30s delay becomes 25-35s)
                jitter_delay = random.uniform(request_delay * 0.8, request_delay * 1.2)
                if progress_cb: progress_cb(f"⏳ Waiting {int(jitter_delay)}s (Safety Mode)...", None)
                time.sleep(jitter_delay)
            else:
                # Minimum jitter even if delay is 0
                time.sleep(random.uniform(0, 1.5))
            
            info_cmd = ytdlp + ['--rm-cache-dir', '--print-json', '--no-download', '--no-playlist']
            if request_delay > 0:
                info_cmd += ['--sleep-interval', str(int(request_delay))]
            
            if cookie_file and os.path.isfile(cookie_file):
                info_cmd += ['--cookies', cookie_file]
            elif browser:
                info_cmd += ['--cookies-from-browser', browser]
            info_cmd.append(url)
            
            # Use run to capture both stdout and stderr for better error reporting
            proc = subprocess.run(info_cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
            if proc.returncode != 0:
                raise RuntimeError(_clean_err(proc.stderr))

            info = json.loads(proc.stdout.splitlines()[0])
            vid_id = info.get('id')
            title = info.get('title', url)
            
            # Label prefix for parallel logs
            prefix = f"[{title[:30]}...]"
            
            # Check for duplicate
            if check_exists_cb and vid_id:
                if check_exists_cb(vid_id, title=title):
                    with lock:
                        state['completed_count'] += 1
                        pct = int(state['completed_count'] / total * 100)
                    if progress_cb: progress_cb(f'  ✓ [Duplicate] {prefix} Hyperlink already downloaded (Skipped)', pct)
                    return {'url': url, 'id': vid_id, 'status': 'skipped', 'title': title}

            # 3. Download
            if progress_cb: progress_cb(f'  ↓ Starting: {prefix}', None)
            
            # Inject delay for sub-processes
            info['request_delay'] = request_delay
            
            res = _download_yt_single(
                url, quality, output_dir, custom_dir, audio_only, download_subs, download_thumb, ytdlp, 
                progress_cb, prefix, info, browser=browser, cookie_file=cookie_file
            )
            
            with lock:
                state['completed_count'] += 1
                pct = int(state['completed_count'] / total * 100)
            
            if progress_cb: progress_cb(f'  ✓ Done: {prefix}', pct)
            return res
                
        except Exception as exc:
            with lock:
                state['completed_count'] += 1
                pct = int(state['completed_count'] / total * 100)
            
            err_msg = str(exc)
            if progress_cb: progress_cb(f'  ✗ Error: {url} -> {err_msg}', pct)
            return {'url': url, 'status': 'error', 'error': err_msg}

    # Parallel Execute
    concurrency = max(1, min(10, concurrency))
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(worker, i, url): url for i, url in enumerate(expanded_urls, 1)}
        for future in as_completed(futures):
            results.append(future.result())

    # Removed abend logic for resiliency

    return results

def _download_yt_single(url, quality, output_dir, custom_dir, audio_only, 
                        download_subs, download_thumb, ytdlp, progress_cb, prefix, info, browser=None, cookie_file=None):
    """Download one YouTube URL."""

    title    = info.get('title', 'unknown')
    vid_id   = info.get('id', 'unknown')
    channel  = re.sub(r'[^\w.-]', '_', info.get('channel', info.get('uploader', 'youtube')))
    duration = info.get('duration', 0)

    if custom_dir:
        channel_dir = output_dir
    else:
        channel_dir = os.path.join(output_dir, channel)
    os.makedirs(channel_dir, exist_ok=True)
    
    # We use a temp name to avoid any collision before we rename
    # Adding id to template helps us find the specific files later
    out_template = os.path.join(channel_dir, f'%(title).150s [%(id)s].%(ext)s')

    dl_cmd = ytdlp + ['--no-playlist', '--rm-cache-dir', '--output', out_template, '--merge-output-format', 'mp4', '--prefer-free-formats', '--socket-timeout', '30']
    
    # Pass delay to yt-dlp internal requests
    if info.get('request_delay'):
        try:
            d = int(info['request_delay'])
            dl_cmd += ['--sleep-interval', str(d), '--max-sleep-interval', str(d + 5)]
        except: pass

    if cookie_file and os.path.isfile(cookie_file):
        dl_cmd += ['--cookies', cookie_file]
    elif browser:
        dl_cmd += ['--cookies-from-browser', browser]
    
    if audio_only:
        dl_cmd += ['-x', '--audio-format', 'mp3', '--audio-quality', '0']
    else:
        if quality == 'best':
            # Priority: MP4 Best -> Any Best
            fmt = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best'
        else:
            # Priority: Requested Height MP4 -> Requested Height Any -> ANY Best Available below/at height -> ABSOLUTE BEST
            fmt = (f'bestvideo[height<={quality}][ext=mp4]+bestaudio[ext=m4a]/'
                   f'bestvideo[height<={quality}]+bestaudio/'
                   f'best[height<={quality}]/'
                   f'best')
        dl_cmd += ['--format', fmt, '--merge-output-format', 'mp4']
        
        if download_thumb:
            dl_cmd += ['--write-thumbnail', '--convert-thumbnails', 'jpg']
            
        if download_subs:
            # Download English subs (manual or auto)
            dl_cmd += ['--write-subs', '--write-auto-subs', '--sub-langs', 'en,en-orig,en-US,en-GB', '--convert-subs', 'srt']

    dl_cmd.append(url)

    proc = subprocess.Popen(
        dl_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding='utf-8', errors='replace', bufsize=1
    )
    last_lines = []
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            last_lines.append(line)
            if len(last_lines) > 10: last_lines.pop(0)
            
            if progress_cb:
                m = re.search(r'\[download\]\s+([\d.]+)%', line)
                pct = None
                progress_cb(f"{prefix} {line}", pct)
    proc.wait()
    
    if proc.returncode != 0:
        err_out = "\n".join(last_lines)
        raise RuntimeError(_clean_err(err_out))

    # ── Post-download finding and renaming ────────────────────────
    out_file = None
    ext = 'mp3' if audio_only else 'mp4'
    
    # Try to find the files based on the unique ID in the filename
    base_name = None
    subs_file = None
    thumb_file = None
    
    for fname in os.listdir(channel_dir):
        if f'[{vid_id}]' in fname:
            fpath = os.path.join(channel_dir, fname)
            if fname.endswith(f'.{ext}'):
                out_file = fpath
                base_name = fname.rsplit(' [', 1)[0] # Title part
            elif fname.endswith('.srt'):
                subs_file = fpath
            elif fname.endswith('.jpg') or fname.endswith('.webp'):
                # yt-dlp might leave .webp if conversion failed, but we asked for .jpg
                thumb_file = fpath

    # Apply renaming convention: TITLE_<engsub> and TITLE_<thumbnail>
    if base_name:
        if subs_file and download_subs:
            new_subs_name = os.path.join(channel_dir, f"{base_name}_<engsub>.srt")
            try:
                if os.path.exists(new_subs_name): os.remove(new_subs_name)
                os.rename(subs_file, new_subs_name)
                subs_file = new_subs_name
            except: pass
            
        if thumb_file and download_thumb:
            new_thumb_name = os.path.join(channel_dir, f"{base_name}_<thumbnail>.jpg")
            try:
                if os.path.exists(new_thumb_name): os.remove(new_thumb_name)
                os.rename(thumb_file, new_thumb_name)
                thumb_file = new_thumb_name
            except: pass

    return {
        'url':       url,
        'id':        vid_id,
        'title':     title,
        'channel':   channel,
        'duration':  duration,
        'file_path': out_file,
        'thumbnail': thumb_file,
        'subtitles': subs_file,
        'status':    'ok',
        'audio_only': audio_only,
    }
