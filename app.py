"""
Nikethan Reels Toolkit — Flask App
Password: nikethan
"""
import os, json, uuid, threading, subprocess, shutil, sys, tkinter as tk
from tkinter import filedialog
from datetime import datetime
import datetime as _dt_module
from flask import (Flask, render_template, redirect, url_for,
                   request, session, flash, g, jsonify, Response,
                   send_file, abort)

from db import init_db, get_db
from modules.cuda_check import detect_cuda
from modules.downloader import download_reels
from modules.metadata import extract_metadata_from_json
from modules.watermarker import apply_watermark_to_folder, fetch_ig_watermark
from modules.splitter import split_equal, split_trailer
from modules.instagram_social import (lookup_user, extract_followers,
                                      follow_users, unfollow_users,
                                      get_follow_status, get_unfollow_status)
from modules.audio_tools import (extract_mp3, extract_mp3_from_folder,
                                 merge_audio_video, batch_merge)
from modules.youtube_downloader import download_youtube

# Upload temp dir for file uploads
UPLOAD_TEMP = os.path.join(os.path.dirname(__file__), 'tmp_uploads')
os.makedirs(UPLOAD_TEMP, exist_ok=True)

# ── App setup ─────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'nikethan-secret-2026')

BASE_DIR      = os.path.dirname(__file__)
DOWNLOADS_DIR = os.path.join(BASE_DIR, 'downloads')
COOKIES_FILE  = os.path.join(BASE_DIR, 'cookies.txt')
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# Global job store  { job_id: { events: [], done: bool, ... } }
JOBS: dict = {}

# Detect CUDA once at startup
CUDA_INFO = detect_cuda()

# ── Template helpers ──────────────────────────────────────────
@app.template_filter('from_json')
def from_json_filter(s):
    try: return json.loads(s or '[]')
    except: return []

@app.before_request
def inject_globals():
    g.cuda_available   = CUDA_INFO.get('available', False)
    g.reel_count       = 0
    if session.get('logged_in'):
        db = get_db()
        row = db.execute('SELECT COUNT(*) FROM reels').fetchone()
        g.reel_count = row[0] if row else 0

def system_status():
    ffmpeg = shutil.which('ffmpeg')
    ytdlp  = shutil.which('yt-dlp')
    ffmpeg_ver = ''
    ytdlp_ver  = ''
    try:
        ffmpeg_ver = subprocess.check_output(['ffmpeg', '-version'],
            stderr=subprocess.STDOUT, text=True).splitlines()[0].split('version')[1].split()[0]
    except: pass
    try:
        ytdlp_ver = subprocess.check_output([sys.executable, '-m', 'yt_dlp', '--version'],
            text=True).strip()[:12]
    except: pass
    return {
        'ffmpeg_ok':      bool(ffmpeg),
        'ffmpeg_version': ffmpeg_ver or 'OK',
        'ytdlp_ok':       bool(ytdlp),
        'ytdlp_version':  ytdlp_ver or 'OK',
        'cookies_ok':     os.path.isfile(COOKIES_FILE),
    }

# ── Auth ──────────────────────────────────────────────────────
@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('logged_in'):
        return redirect(url_for('index'))
    if request.method == 'POST':
        pwd = request.form.get('password', '')
        if pwd == 'nikethan':
            session['logged_in'] = True
            flash('Welcome back, Nikethan!', 'success')
            return redirect(url_for('index'))
        flash('Incorrect password. Please try again.', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

def require_login():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

# ── Pages ─────────────────────────────────────────────────────
@app.route('/')
def index():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    db   = get_db()
    sys  = system_status()
    reels_recent = db.execute(
        'SELECT * FROM reels ORDER BY downloaded_at DESC LIMIT 5'
    ).fetchall()
    row_wm    = db.execute('SELECT COUNT(*) FROM reels WHERE watermarked=1').fetchone()
    row_total = db.execute('SELECT COUNT(*) FROM reels').fetchone()
    row_tags  = db.execute("SELECT COUNT(DISTINCT value) FROM (SELECT json_each.value FROM reels, json_each(tags) WHERE tags != '[]' AND tags IS NOT NULL)").fetchone()

    # Count folders
    folders = [d for d in os.listdir(DOWNLOADS_DIR)
               if os.path.isdir(os.path.join(DOWNLOADS_DIR, d))
               and d != 'watermarks']

    # Total size
    total_bytes = sum(
        f.st_size
        for dirpath, _, files in os.walk(DOWNLOADS_DIR)
        for f in [os.stat(os.path.join(dirpath, fname)) for fname in files
                  if fname.endswith('.mp4')]
    ) if os.path.exists(DOWNLOADS_DIR) else 0

    stats = {
        'total_reels'  : row_total[0] if row_total else 0,
        'total_tags'   : row_tags[0]  if row_tags  else 0,
        'watermarked'  : row_wm[0]    if row_wm    else 0,
        'splits'       : 0,
        'folders'      : len(folders),
        'total_size_gb': f'{total_bytes / 1024**3:.2f}',
    }
    return render_template('index.html',
        stats=stats,
        system=sys,
        recent_reels=[dict(r) for r in reels_recent]
    )

def _get_settings_dict():
    rows = get_db().execute('SELECT key, value FROM settings').fetchall()
    return {r['key']: r['value'] for r in rows}

@app.route('/download')
def download_page():
    if not session.get('logged_in'): return redirect(url_for('login'))
    return render_template('download.html', system=system_status(), settings=_get_settings_dict())

@app.route('/metadata')
def metadata_page():
    if not session.get('logged_in'): return redirect(url_for('login'))
    db = get_db()
    reels = [dict(r) for r in db.execute('SELECT * FROM reels ORDER BY downloaded_at DESC').fetchall()]
    total_tags     = sum(len(json.loads(r.get('tags') or '[]')) for r in reels)
    total_accounts = len({r.get('account') for r in reels if r.get('account')})
    return render_template('metadata.html',
        reels=reels,
        total_tags=total_tags,
        total_accounts=total_accounts,
    )

@app.route('/watermark')
def watermark_page():
    if not session.get('logged_in'): return redirect(url_for('login'))
    folders = []
    if os.path.isdir(DOWNLOADS_DIR):
        for name in os.listdir(DOWNLOADS_DIR):
            fpath = os.path.join(DOWNLOADS_DIR, name)
            if os.path.isdir(fpath) and name != 'watermarks':
                mp4s = [f for f in os.listdir(fpath) if f.endswith('.mp4')]
                size = sum(os.path.getsize(os.path.join(fpath, f)) for f in mp4s) if mp4s else 0
                folders.append({
                    'name':    name,
                    'path':    fpath,
                    'count':   len(mp4s),
                    'size_mb': f'{size / 1024**2:.1f}',
                })
    return render_template('watermark.html', folders=folders)

@app.route('/split/equal')
def split_equal_page():
    if not session.get('logged_in'): return redirect(url_for('login'))
    return render_template('split_equal.html')

@app.route('/split/trailer')
def split_trailer_page():
    if not session.get('logged_in'): return redirect(url_for('login'))
    return render_template('split_trailer.html')

@app.route('/extract-audio')
def extract_audio_page():
    if not session.get('logged_in'): return redirect(url_for('login'))
    return render_template('extract_audio.html', settings=_get_settings_dict())

@app.route('/merge-audio')
def merge_audio_page():
    if not session.get('logged_in'): return redirect(url_for('login'))
    return render_template('merge_audio.html', settings=_get_settings_dict())

@app.route('/youtube')
def youtube_page():
    if not session.get('logged_in'): return redirect(url_for('login'))
    return render_template('youtube.html', settings=_get_settings_dict())

@app.route('/settings')
def settings_page():
    if not session.get('logged_in'): return redirect(url_for('login'))
    db = get_db()

    # Load all settings from DB
    rows = db.execute('SELECT key, value FROM settings').fetchall()
    settings = {r['key']: r['value'] for r in rows}
    # Convert numeric types
    for k in ('wm_fontsize','wm_opacity','follow_per_click','follow_per_hour','follow_delay'):
        if k in settings:
            try: settings[k] = int(settings[k])
            except: pass

    # Cookie status
    cookie_status = {'ok': False, 'path': COOKIES_FILE, 'size_kb': 0, 'lines': 0, 'modified': ''}
    if os.path.isfile(COOKIES_FILE):
        stat = os.stat(COOKIES_FILE)
        with open(COOKIES_FILE, 'r', encoding='utf-8', errors='ignore') as f:
            lines = [l for l in f if l.strip() and not l.startswith('#')]
        cookie_status.update({
            'ok':       True,
            'size_kb':  f'{stat.st_size / 1024:.1f}',
            'lines':    len(lines),
            'modified': _dt_module.datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M'),
        })

    return render_template('settings.html', settings=settings, cookie_status=cookie_status)

@app.route('/instagram')
def instagram_page():
    if not session.get('logged_in'): return redirect(url_for('login'))
    db = get_db()
    cookie_ok   = os.path.isfile(COOKIES_FILE)
    saved_lists = [dict(r) for r in db.execute(
        'SELECT * FROM ig_extractions ORDER BY extracted_at DESC'
    ).fetchall()]
    return render_template('instagram.html', cookie_ok=cookie_ok, saved_lists=saved_lists)

# ── Thumbnail / watermark preview ─────────────────────────────
@app.route('/thumb/<reel_id>')
def serve_thumbnail(reel_id):
    if not session.get('logged_in'): abort(401)
    db   = get_db()
    row  = db.execute('SELECT thumbnail FROM reels WHERE id=?', (reel_id,)).fetchone()
    if row and row['thumbnail'] and os.path.isfile(row['thumbnail']):
        return send_file(row['thumbnail'])
    abort(404)

@app.route('/wm-preview/<path:wm_path>')
def serve_wm_preview(wm_path):
    if not session.get('logged_in'): abort(401)
    full = os.path.normpath(wm_path)
    if os.path.isfile(full):
        return send_file(full)
    abort(404)

# ══════════════════════════════════════════════════════════════
#  API — Download
# ══════════════════════════════════════════════════════════════
def _make_job(job_id):
    JOBS[job_id] = {'events': [], 'done': False, 'result': None}
    return JOBS[job_id]

def _emit(job, line, pct=None, status=None):
    event = {'line': line}
    if pct is not None:   event['pct'] = pct
    if status is not None: event['status'] = status
    job['events'].append(('progress', event))

def _finish(job, message, **extra):
    job['result'] = {'message': message, **extra}
    job['events'].append(('done', job['result']))
    job['done'] = True

@app.route('/api/download', methods=['POST'])
def api_download():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data    = request.get_json()
    urls    = data.get('urls', [])
    quality = data.get('quality', 'best')
    out_dir = data.get('output_dir', '').strip()
    if not urls:
        return jsonify({'error': 'No URLs provided'}), 400

    job_id = str(uuid.uuid4())
    job    = _make_job(job_id)

    def run():
        custom_dir = True
        _out = out_dir
        if not _out:
            _out = _read_setting('dir_ig') or DOWNLOADS_DIR
            custom_dir = False
        results = download_reels(
            urls=urls,
            quality=quality,
            cookies_file=COOKIES_FILE,
            downloads_dir=_out,
            custom_dir=custom_dir,
            progress_cb=lambda line, pct=None: _emit(job, line, pct),
            db_cb=_save_reel_to_db,
        )
        _finish(job, f'Downloaded {len(results)} reel(s)', results=results)

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'job_id': job_id})

def _save_reel_to_db(info: dict):
    """Thread-safe DB insert — uses a direct connection, not Flask g."""
    import sqlite3
    db_path = os.path.join(BASE_DIR, 'reels_db.sqlite')
    con = sqlite3.connect(db_path)
    try:
        con.execute('''
            INSERT OR REPLACE INTO reels
              (id, url, account, title, caption, tags, mentions,
               duration, file_path, thumbnail, downloaded_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ''', (
            info.get('id'), info.get('url'), info.get('account'),
            info.get('title'), info.get('caption'),
            json.dumps(info.get('tags', [])),
            json.dumps(info.get('mentions', [])),
            info.get('duration'), info.get('file_path'),
            info.get('thumbnail'),
            datetime.utcnow().isoformat(),
        ))
        con.commit()
    finally:
        con.close()


def _read_setting(key: str, default: str = '') -> str:
    """Thread-safe settings read via direct sqlite3 (not Flask g)."""
    import sqlite3
    db_path = os.path.join(BASE_DIR, 'reels_db.sqlite')
    try:
        con = sqlite3.connect(db_path)
        row = con.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        con.close()
        return row[0].strip() if row and row[0] else default
    except Exception:
        return default


@app.route('/api/download/progress/<job_id>')
def api_download_progress(job_id):
    return _sse_stream(job_id)

# ══════════════════════════════════════════════════════════════
#  API — Utils
# ══════════════════════════════════════════════════════════════
@app.route('/api/utils/pick-folder')
def api_pick_folder():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    try:
        code = "import tkinter as tk, tkinter.filedialog as fd; root=tk.Tk(); root.attributes('-topmost', True); root.withdraw(); print(fd.askdirectory(parent=root, title='Select Output Folder'))"
        out = subprocess.check_output([sys.executable, '-c', code], text=True).strip()
        if out == "None" or not out: out = ""
        return jsonify({'folder': out})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/utils/test-gpu')
def api_test_gpu():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    import modules.cuda_check
    modules.cuda_check._cache = None
    res = modules.cuda_check.detect_cuda()
    return jsonify(res)

# ══════════════════════════════════════════════════════════════
#  API — Metadata
# ══════════════════════════════════════════════════════════════
@app.route('/api/metadata/<reel_id>', methods=['GET'])
def api_metadata_single(reel_id):
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    db  = get_db()
    row = db.execute('SELECT * FROM reels WHERE id=?', (reel_id,)).fetchone()
    if not row: abort(404)
    return jsonify(dict(row))

@app.route('/api/metadata/<reel_id>', methods=['PATCH'])
def api_metadata_update(reel_id):
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data    = request.get_json()
    caption = data.get('caption', '')
    tags    = data.get('tags', [])
    db = get_db()
    db.execute('UPDATE reels SET caption=?, tags=? WHERE id=?',
               (caption, json.dumps(tags), reel_id))
    db.commit()
    # Also update sidecar JSON
    row = db.execute('SELECT file_path FROM reels WHERE id=?', (reel_id,)).fetchone()
    if row and row['file_path']:
        sidecar = row['file_path'].replace('.mp4', '.json')
        if os.path.isfile(sidecar):
            with open(sidecar, 'r', encoding='utf-8') as f:
                meta = json.load(f)
            meta['caption'] = caption
            meta['tags']    = tags
            with open(sidecar, 'w', encoding='utf-8') as f:
                json.dump(meta, f, indent=2, ensure_ascii=False)
    return jsonify({'ok': True})

@app.route('/api/metadata/export')
def api_metadata_export():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    import csv, io
    db   = get_db()
    rows = db.execute('SELECT * FROM reels').fetchall()
    buf  = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=[k for k in dict(rows[0]).keys()] if rows else [])
    writer.writeheader()
    for r in rows: writer.writerow(dict(r))
    output = buf.getvalue()
    return Response(output, mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment; filename=reels_metadata.csv'})

# ══════════════════════════════════════════════════════════════
#  API — Watermark
# ══════════════════════════════════════════════════════════════
@app.route('/api/folders')
def api_folders():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    folders = []
    for name in os.listdir(DOWNLOADS_DIR):
        fpath = os.path.join(DOWNLOADS_DIR, name)
        if os.path.isdir(fpath) and name != 'watermarks':
            mp4s = [f for f in os.listdir(fpath) if f.endswith('.mp4')]
            folders.append({'name': name, 'path': fpath, 'count': len(mp4s)})
    return jsonify(folders)

@app.route('/api/watermark/fetch', methods=['POST'])
def api_wm_fetch():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json()
    ig_url = data.get('instagram_url', '').strip()
    if not ig_url: return jsonify({'error': 'No URL'}), 400
    wm_dir = os.path.join(DOWNLOADS_DIR, 'watermarks')
    os.makedirs(wm_dir, exist_ok=True)
    result = fetch_ig_watermark(ig_url, wm_dir, cookies_file=COOKIES_FILE)
    return jsonify(result)

@app.route('/api/watermark/apply', methods=['POST'])
def api_wm_apply():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401

    folder       = request.form.get('folder', '')
    position     = request.form.get('position', 'BR')
    opacity      = float(request.form.get('opacity', 0.75))
    scale        = float(request.form.get('scale', 0.15))
    output_mode  = request.form.get('output_mode', 'new_folder')
    watermark_path = request.form.get('watermark_path', '')
    mode         = request.form.get('mode', 'upload')

    if not folder or not os.path.isdir(folder):
        return jsonify({'error': 'Invalid folder'}), 400

    # Handle file upload
    if mode == 'upload' and 'watermark_file' in request.files:
        wm_file = request.files['watermark_file']
        wm_dir  = os.path.join(DOWNLOADS_DIR, 'watermarks')
        os.makedirs(wm_dir, exist_ok=True)
        wm_dest = os.path.join(wm_dir, f'__upload_{uuid.uuid4().hex[:8]}__' + os.path.splitext(wm_file.filename)[1])
        wm_file.save(wm_dest)
        watermark_path = wm_dest

    if not watermark_path or not os.path.isfile(watermark_path):
        return jsonify({'error': 'No valid watermark image'}), 400

    job_id = str(uuid.uuid4())
    job    = _make_job(job_id)

    def run():
        count = apply_watermark_to_folder(
            folder=folder,
            watermark_path=watermark_path,
            position=position,
            opacity=opacity,
            scale=scale,
            output_mode=output_mode,
            progress_cb=lambda line, pct=None: _emit(job, line, pct),
        )
        _finish(job, f'Watermarked {count} files', count=count)

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'job_id': job_id})

@app.route('/api/watermark/progress/<job_id>')
def api_wm_progress(job_id):
    return _sse_stream(job_id)

# ══════════════════════════════════════════════════════════════
#  API — Settings
# ══════════════════════════════════════════════════════════════
def _get_settings():
    db = get_db()
    rows = db.execute('SELECT key, value FROM settings').fetchall()
    return {r['key']: r['value'] for r in rows}

def _save_setting(key, value):
    db = get_db()
    db.execute('INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)', (key, str(value)))
    db.commit()

@app.route('/api/settings/directories/save', methods=['POST'])
def api_directories_save():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json()
    _save_setting('dir_ig', data.get('dir_ig', '').strip())
    _save_setting('dir_yt', data.get('dir_yt', '').strip())
    return jsonify({'ok': True})

@app.route('/api/settings/cookies/save', methods=['POST'])
def api_cookies_save():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data    = request.get_json()
    content = data.get('content', '').strip()
    if not content: return jsonify({'error': 'Empty content'}), 400
    lines = [l for l in content.splitlines() if l.strip() and not l.startswith('#')]
    with open(COOKIES_FILE, 'w', encoding='utf-8') as f:
        f.write(content)
    return jsonify({'ok': True, 'lines': len(lines)})

@app.route('/api/settings/cookies/preview')
def api_cookies_preview():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    if not os.path.isfile(COOKIES_FILE): return jsonify({'content': '(file not found)'})
    with open(COOKIES_FILE, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read(4096)  # first 4KB preview
    return jsonify({'content': content})

@app.route('/api/settings/cookies/delete', methods=['POST'])
def api_cookies_delete():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    if os.path.isfile(COOKIES_FILE): os.remove(COOKIES_FILE)
    return jsonify({'ok': True})

@app.route('/api/settings/watermark/save', methods=['POST'])
def api_wm_settings_save():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json()
    for key in ('wm_text','wm_font','wm_fontsize','wm_color','wm_opacity','wm_position'):
        if key in data:
            _save_setting(key, data[key])
    return jsonify({'ok': True})

@app.route('/api/settings/ratelimits/save', methods=['POST'])
def api_ratelimits_save():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json()
    for key in ('follow_per_click','follow_per_hour','follow_delay'):
        if key in data:
            val = int(data[key])
            if key in ('follow_per_click','follow_per_hour'): val = min(10, max(1, val))
            _save_setting(key, val)
    return jsonify({'ok': True})

# ══════════════════════════════════════════════════════════════
#  API — Instagram Social
# ══════════════════════════════════════════════════════════════
@app.route('/api/instagram/lookup', methods=['POST'])
def api_ig_lookup():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data     = request.get_json()
    username = data.get('username', '').strip()
    if not username: return jsonify({'error': 'No username'}), 400
    result = lookup_user(username, COOKIES_FILE)
    return jsonify(result)

@app.route('/api/instagram/extract', methods=['POST'])
def api_ig_extract():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data      = request.get_json()
    username  = data.get('username', '').strip()
    list_type = data.get('list_type', 'followers')
    max_users = int(data.get('max_users', 500))

    if not username: return jsonify({'error': 'No username'}), 400

    job_id = str(uuid.uuid4())
    job    = _make_job(job_id)

    def run():
        users = extract_followers(
            username=username,
            cookies_file=COOKIES_FILE,
            max_users=max_users,
            list_type=list_type,
            progress_cb=lambda line, pct=None: _emit(job, line, pct),
        )
        # Save to DB and JSON file
        extract_id = str(uuid.uuid4())
        ig_dir     = os.path.join(DOWNLOADS_DIR, '_ig_extractions')
        os.makedirs(ig_dir, exist_ok=True)
        data_path  = os.path.join(ig_dir, f'{username}_{list_type}_{extract_id[:8]}.json')
        with open(data_path, 'w', encoding='utf-8') as fp:
            json.dump(users, fp, ensure_ascii=False, indent=2)
        db = get_db()
        db.execute(
            'INSERT INTO ig_extractions (id,username,list_type,count,data_path,extracted_at) VALUES (?,?,?,?,?,?)',
            (extract_id, username, list_type, len(users), data_path, datetime.utcnow().isoformat())
        )
        db.commit()
        _finish(job, f'Extracted {len(users)} users', users=users, extract_id=extract_id)

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'job_id': job_id})

@app.route('/api/instagram/progress/<job_id>')
def api_ig_progress(job_id):
    return _sse_stream(job_id)

@app.route('/api/instagram/follow', methods=['POST'])
def api_ig_follow():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data          = request.get_json()
    usernames     = data.get('usernames', [])
    skip_existing = data.get('skip_existing', True)
    skip_private  = data.get('skip_private', False)

    if not usernames: return jsonify({'error': 'No usernames'}), 400

    # Pre-flight cooldown check — return immediately without starting a job
    status = get_follow_status()
    if status['in_cooldown']:
        return jsonify({
            'error':        'Cooling period active',
            'in_cooldown':  True,
            'seconds_left': status['seconds_left'],
        }), 429

    # Cap at 10 regardless of what's sent
    usernames = usernames[:10]

    job_id = str(uuid.uuid4())
    job    = _make_job(job_id)

    def run():
        result = follow_users(
            usernames=usernames,
            cookies_file=COOKIES_FILE,
            skip_existing=skip_existing,
            skip_private=skip_private,
            progress_cb=lambda line, pct=None: _emit(job, line, pct),
        )
        _finish(job, f'Followed {result["followed"]} users', **result)

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'job_id': job_id})

@app.route('/api/instagram/follow/status')
def api_ig_follow_status():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    return jsonify(get_follow_status())

@app.route('/api/instagram/unfollow', methods=['POST'])
def api_ig_unfollow():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data      = request.get_json()
    usernames = data.get('usernames', [])
    if not usernames: return jsonify({'error': 'No usernames'}), 400

    # Pre-flight cooldown check
    status = get_unfollow_status()
    if status['in_cooldown']:
        return jsonify({
            'error':       'Cooling period active',
            'in_cooldown':  True,
            'seconds_left': status['seconds_left'],
        }), 429

    usernames = usernames[:10]

    job_id = str(uuid.uuid4())
    job    = _make_job(job_id)

    def run():
        result = unfollow_users(
            usernames=usernames,
            cookies_file=COOKIES_FILE,
            progress_cb=lambda line, pct=None: _emit(job, line, pct),
        )
        _finish(job, f'Unfollowed {result["unfollowed"]} users', **result)

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'job_id': job_id})

@app.route('/api/instagram/unfollow/status')
def api_ig_unfollow_status():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    return jsonify(get_unfollow_status())

@app.route('/api/instagram/lists')
def api_ig_lists():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    db   = get_db()
    rows = db.execute('SELECT * FROM ig_extractions ORDER BY extracted_at DESC').fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/instagram/export/<extract_id>/csv')
def api_ig_export_csv(extract_id):
    if not session.get('logged_in'): abort(401)
    db  = get_db()
    row = db.execute('SELECT * FROM ig_extractions WHERE id=?', (extract_id,)).fetchone()
    if not row or not row['data_path'] or not os.path.isfile(row['data_path']): abort(404)
    with open(row['data_path'], 'r', encoding='utf-8') as f:
        users = json.load(f)
    import csv, io
    buf    = io.StringIO()
    fields = ['username','full_name','user_id','followers_count','is_private','list_type']
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    for u in users: writer.writerow(u)
    return Response(buf.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename={row["username"]}_{row["list_type"]}.csv'})

@app.route('/api/instagram/lists/delete', methods=['POST'])
def api_ig_list_delete():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json()
    eid  = data.get('id')
    if not eid: return jsonify({'error': 'No id'}), 400
    db   = get_db()
    row  = db.execute('SELECT data_path FROM ig_extractions WHERE id=?', (eid,)).fetchone()
    if row and row['data_path'] and os.path.isfile(row['data_path']):
        os.remove(row['data_path'])
    db.execute('DELETE FROM ig_extractions WHERE id=?', (eid,))
    db.commit()
    return jsonify({'ok': True})

# ══════════════════════════════════════════════════════════════
#  API — Split
# ══════════════════════════════════════════════════════════════
@app.route('/api/split/equal', methods=['POST'])
def api_split_equal():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    video    = request.files.get('video')
    n        = int(request.form.get('n', 30))
    use_cuda = request.form.get('use_cuda', '0') == '1'

    if not video: return jsonify({'error': 'No video'}), 400

    # Save upload to temp
    tmp_dir = os.path.join(BASE_DIR, 'tmp_uploads')
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_path = os.path.join(tmp_dir, f'{uuid.uuid4().hex}_{video.filename}')
    video.save(tmp_path)

    job_id = str(uuid.uuid4())
    job    = _make_job(job_id)

    def run():
        out_dir = os.path.splitext(tmp_path)[0] + '_splits'
        files = split_equal(
            input_path=tmp_path,
            n=n,
            out_dir=out_dir,
            use_cuda=use_cuda and CUDA_INFO['available'],
            progress_cb=lambda line, pct=None: _emit(job, line, pct),
        )
        _finish(job, f'Created {len(files)} segments', files=[{'name': os.path.basename(f)} for f in files])

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'job_id': job_id})

@app.route('/api/split/trailer', methods=['POST'])
def api_split_trailer():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    video    = request.files.get('video')
    clips    = json.loads(request.form.get('clips', '[]'))
    concat   = request.form.get('concat', '0') == '1'
    use_cuda = request.form.get('use_cuda', '0') == '1'

    if not video:  return jsonify({'error': 'No video'}), 400
    if not clips:  return jsonify({'error': 'No clips defined'}), 400

    tmp_dir = os.path.join(BASE_DIR, 'tmp_uploads')
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_path = os.path.join(tmp_dir, f'{uuid.uuid4().hex}_{video.filename}')
    video.save(tmp_path)

    job_id = str(uuid.uuid4())
    job    = _make_job(job_id)

    def run():
        out_dir = os.path.splitext(tmp_path)[0] + '_trailer'
        files = split_trailer(
            input_path=tmp_path,
            clips=clips,
            out_dir=out_dir,
            concat=concat,
            use_cuda=use_cuda and CUDA_INFO['available'],
            progress_cb=lambda line, pct=None: _emit(job, line, pct),
        )
        _finish(job, f'Extracted {len(files)} clip(s)',
                files=[{'name': os.path.basename(f['path']), 'label': f['label']} for f in files])

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'job_id': job_id})

@app.route('/api/split/progress/<job_id>')
def api_split_progress(job_id):
    return _sse_stream(job_id)

# ── SSE helper ────────────────────────────────────────────────
def _sse_stream(job_id):
    job = JOBS.get(job_id)
    if not job:
        return Response('data: {"error":"Job not found"}\n\n',
                        mimetype='text/event-stream')

    def generate():
        sent = 0
        import time
        while True:
            events = job['events']
            while sent < len(events):
                etype, edata = events[sent]
                yield f'event: {etype}\ndata: {json.dumps(edata)}\n\n'
                sent += 1
            if job['done'] and sent >= len(job['events']):
                break
            time.sleep(0.15)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


# ══════════════════════════════════════════════════════════════
#  API — YouTube Download
# ══════════════════════════════════════════════════════════════
@app.route('/api/youtube/download', methods=['POST'])
def api_youtube_download():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data       = request.get_json()
    urls       = data.get('urls', [])
    quality    = data.get('quality', '720')
    audio_only = data.get('audio_only', False)
    if not urls: return jsonify({'error': 'No URLs'}), 400

    job_id = str(uuid.uuid4())
    job    = _make_job(job_id)

    def run():
        yt_dir = data.get('output_dir', '').strip()
        custom_dir = True
        if not yt_dir:
            yt_dir = _read_setting('dir_yt') or os.path.join(DOWNLOADS_DIR, '_youtube')
            custom_dir = False
        results = download_youtube(
            urls=urls, quality=quality, output_dir=yt_dir, audio_only=audio_only, custom_dir=custom_dir,
            progress_cb=lambda line, pct=None: _emit(job, line, pct)
        )
        ok = sum(1 for r in results if r.get('status') == 'ok')
        _finish(job, f'Downloaded {ok}/{len(results)} videos', results=results)
    threading.Thread(target=run, daemon=True).start()
    return jsonify({'job_id': job_id})

@app.route('/api/youtube/progress/<job_id>')
def api_youtube_progress(job_id):
    return _sse_stream(job_id)

# ══════════════════════════════════════════════════════════════
#  API — Audio Tools (Extract MP3 + Merge)
# ══════════════════════════════════════════════════════════════
@app.route('/api/audio/upload', methods=['POST'])
def api_audio_upload():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    files = request.files.getlist('files')
    if not files: return jsonify({'error': 'No files'}), 400
    paths = []
    for f in files:
        dest = os.path.join(UPLOAD_TEMP, f.filename)
        f.save(dest)
        paths.append(dest)
    return jsonify({'paths': paths})

@app.route('/api/audio/upload-merge', methods=['POST'])
def api_audio_upload_merge():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    video = request.files.get('video')
    audio = request.files.get('audio')
    if not video or not audio: return jsonify({'error': 'Missing files'}), 400
    vpath = os.path.join(UPLOAD_TEMP, video.filename)
    apath = os.path.join(UPLOAD_TEMP, audio.filename)
    video.save(vpath)
    audio.save(apath)
    return jsonify({'video_path': vpath, 'audio_path': apath})

@app.route('/api/audio/scan-folder', methods=['POST'])
def api_audio_scan_folder():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    path = request.get_json().get('path', '').strip()
    if not os.path.isdir(path): return jsonify({'error': 'Folder not found'}), 404
    exts  = ('.mp4','.mov','.avi','.mkv','.webm','.m4v')
    count = sum(1 for f in os.listdir(path) if f.lower().endswith(exts))
    return jsonify({'count': count, 'path': path})

@app.route('/api/audio/extract', methods=['POST'])
def api_audio_extract():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data       = request.get_json()
    bitrate    = data.get('bitrate', '192k')
    output_dir = data.get('output_dir', '').strip() or None
    file_paths = data.get('file_paths')
    folder     = data.get('folder')

    job_id = str(uuid.uuid4())
    job    = _make_job(job_id)

    def run():
        source = data.get('source')

        if source == 'youtube':
            yt_urls = data.get('yt_urls', [])
            out = output_dir or os.path.join(DOWNLOADS_DIR, '_youtube_mp3')
            os.makedirs(out, exist_ok=True)
            results = download_youtube(
                urls=yt_urls, quality='best', output_dir=out, audio_only=True,
                progress_cb=lambda l, p=None: _emit(job, l, p)
            )
            # rename yt_dlp output status 'ok' -> map properly for UI
            for r in results: r['output'] = r.get('id') or r.get('url')
        elif folder:
            out = output_dir or os.path.join(folder, 'mp3_output')
            os.makedirs(out, exist_ok=True)
            exts = ('.mp4','.mov','.avi','.mkv','.webm','.m4v','.ts','.mts','.m2ts','.3gp','.flv','.wmv')
            vids = [os.path.join(folder, f) for f in os.listdir(folder)
                    if f.lower().endswith(exts)]
            results = extract_mp3(vids, out, bitrate,
                                  progress_cb=lambda l, p=None: _emit(job, l, p))
        else:
            out = output_dir or os.path.join(UPLOAD_TEMP, 'mp3_output')
            results = extract_mp3(file_paths or [], out, bitrate,
                                  progress_cb=lambda l, p=None: _emit(job, l, p))
            
        ok = sum(1 for r in results if r.get('status') == 'ok')
        _finish(job, f'Extracted {ok}/{len(results)} files', results=results)
    threading.Thread(target=run, daemon=True).start()
    return jsonify({'job_id': job_id})

@app.route('/api/audio/merge', methods=['POST'])
def api_audio_merge():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json()
    mode = data.get('mode', 'replace')

    job_id = str(uuid.uuid4())
    job    = _make_job(job_id)

    def run():
        if 'video_path' in data:
            out = os.path.join(UPLOAD_TEMP, 'merged_' + os.path.basename(data['video_path']))
            result = merge_audio_video(
                data['video_path'], data['audio_path'], out, mode,
                progress_cb=lambda l, p=None: _emit(job, l, p)
            )
            _finish(job, 'Merge complete', **result, results=[result])
        else:
            vid_dir = data['video_dir']
            aud_dir = data['audio_dir']
            out_dir = data.get('output_dir') or os.path.join(vid_dir, 'merged_output')
            results = batch_merge(vid_dir, aud_dir, out_dir, mode,
                                  progress_cb=lambda l, p=None: _emit(job, l, p))
            ok = sum(1 for r in results if r.get('status') == 'ok')
            _finish(job, f'Merged {ok}/{len(results)}', results=results)
    threading.Thread(target=run, daemon=True).start()
    return jsonify({'job_id': job_id})

@app.route('/api/audio/progress/<job_id>')
def api_audio_progress(job_id):
    return _sse_stream(job_id)

# ══════════════════════════════════════════════════════════════
#  API — Download by Instagram User ID (rate limited 10/2hr)
# ══════════════════════════════════════════════════════════════
from modules.instagram_social import _load_state, _save_state, WINDOW_SECONDS, BATCH_LIMIT
import time as _time

def _get_uid_dl_status():
    state     = _load_state()
    now       = _time.time()
    win_start = state.get('uid_dl_window_start', 0.0)
    count     = state.get('uid_dl_count', 0)
    elapsed   = now - win_start
    if elapsed >= WINDOW_SECONDS:
        return {'in_cooldown': False, 'used': 0, 'remaining': BATCH_LIMIT,
                'window_start': now, 'cooldown_ends': now + WINDOW_SECONDS, 'seconds_left': 0}
    remaining     = max(0, BATCH_LIMIT - count)
    in_cooldown   = count >= BATCH_LIMIT
    cooldown_ends = win_start + WINDOW_SECONDS
    seconds_left  = max(0, int(cooldown_ends - now))
    return {'in_cooldown': in_cooldown, 'used': count, 'remaining': remaining,
            'window_start': win_start, 'cooldown_ends': cooldown_ends, 'seconds_left': seconds_left}

def _record_uid_dl(n):
    state = _load_state()
    now   = _time.time()
    if now - state.get('uid_dl_window_start', 0.0) >= WINDOW_SECONDS:
        state['uid_dl_window_start'] = now
        state['uid_dl_count'] = 0
    state['uid_dl_count'] = state.get('uid_dl_count', 0) + n
    _save_state(state)

@app.route('/api/download/userid/status')
def api_uid_dl_status():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    return jsonify(_get_uid_dl_status())

@app.route('/api/download/userid', methods=['POST'])
def api_download_userid():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data     = request.get_json()
    username = data.get('username', '').strip().replace('@', '')
    quality  = data.get('quality', 'best')
    if not username: return jsonify({'error': 'No username'}), 400

    status = _get_uid_dl_status()
    if status['in_cooldown']:
        return jsonify({'error': 'Cooling period active', 'in_cooldown': True,
                        'seconds_left': status['seconds_left']}), 429

    limit  = min(status['remaining'], 10)
    job_id = str(uuid.uuid4())
    job    = _make_job(job_id)

    def run():
        import re as _re
        base_dir    = data.get('output_dir', '').strip()
        if not base_dir: 
            base_dir = _read_setting('dir_ig') or DOWNLOADS_DIR
            out_dir  = os.path.join(base_dir, username)
        else:
            out_dir  = base_dir
            
        os.makedirs(out_dir, exist_ok=True)

        if quality == 'best':
            fmt = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
        else:
            fmt = f'bestvideo[height<={quality}][ext=mp4]+bestaudio[ext=m4a]/best'

        cmd = [
            sys.executable, '-m', 'yt_dlp', '--rm-cache-dir',
            '--format', fmt,
            '--output', os.path.join(out_dir, '%(id)s.%(ext)s'),
            '--write-info-json', '--no-warnings',
            '--ignore-errors',  # skip unavailable videos
        ]
        if os.path.isfile(COOKIES_FILE):
            cmd += ['--cookies', COOKIES_FILE]
            
        _emit(job, f'Looking up latest {limit} reels for @{username}...')
        from modules.instagram_social import lookup_user
        info = lookup_user(username, COOKIES_FILE)
        
        if 'error' in info:
            _emit(job, f'API Error: {info["error"]}')
            _finish(job, 'Failed to fetch user profile metadata.', error=True)
            return
            
        shortcodes = info.get('recent_posts', [])
        if not shortcodes:
            _emit(job, 'No recent posts found for this user.')
            _finish(job, 'Extraction complete (0 found).', error=True)
            return

        # Cap at requested limit
        shortcodes = shortcodes[:limit]
        urls = [f'https://www.instagram.com/p/{sc}/' for sc in shortcodes]
        cmd.extend(urls)

        downloaded = 0
        results    = []
        _emit(job, f'Downloading up to {limit} reels from @{username}...')

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1)
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                if '[download] Destination:' in line:
                    downloaded += 1
                    _record_uid_dl(1)
                    results.append({'status': 'ok', 'id': str(downloaded)})
                    _emit(job, f'  [{downloaded}/{limit}] Downloading reel...', int(downloaded/limit*90))
                else:
                    _emit(job, line)
        proc.wait()

        # Parse downloaded metadata directly into DB
        for fname in os.listdir(out_dir):
            if fname.endswith('.info.json'):
                json_path = os.path.join(out_dir, fname)
                base = fname[:-10]  # remove .info.json
                meta = extract_metadata_from_json(json_path)
                mp4_path = os.path.join(out_dir, base + '.mp4')
                jpg_path = os.path.join(out_dir, base + '.jpg')
                if os.path.isfile(mp4_path):
                    meta['file_path'] = mp4_path
                if os.path.isfile(jpg_path):
                    meta['thumbnail'] = jpg_path
                meta['account'] = username
                meta['status'] = 'ok'
                _save_reel_to_db(meta)

        final = _get_uid_dl_status()
        _finish(job, f'Downloaded {downloaded} reels from @{username}',
                downloaded=downloaded, results=results,
                in_cooldown=final['in_cooldown'],
                seconds_left=final['seconds_left'],
                remaining=final['remaining'])

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'job_id': job_id})

# ── Boot ──────────────────────────────────────────────────────
if __name__ == '__main__':
    init_db()
    cuda_str = 'CUDA ACTIVE' if CUDA_INFO['available'] else 'CPU Mode'
    print(f'\n  Nikethan Reels Toolkit')
    print(f'  GPU: {cuda_str}')
    print(f'  http://localhost:5055\n')
    app.run(host='0.0.0.0', port=5055, debug=False)
