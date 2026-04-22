"""
Nikethan Reels Toolkit — Flask App
Password: nikethan
"""
import os, json, uuid, threading, subprocess, shutil, sys, sqlite3, re, tkinter as tk
from tkinter import filedialog
from datetime import datetime, timedelta
from datetime import timezone
import datetime as _dt_module
from flask import (Flask, render_template, redirect, url_for,
                   request, session, flash, g, jsonify, Response,
                   send_file, abort)

from db import init_db, get_db, DB_PATH
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
from modules.dependency_manager import init_runtime_path, check_system_status, install_ffmpeg_thread, upgrade_ytdlp_thread

# Safely inject local ffmpeg into runtime PATH before anything else does ffmpeg checks
init_runtime_path()

# Upload temp dir for file uploads
UPLOAD_TEMP = os.path.join(os.path.dirname(__file__), 'tmp_uploads')
os.makedirs(UPLOAD_TEMP, exist_ok=True)

# ── App setup ─────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'nikethan-secret-2026')

BASE_DIR      = os.path.dirname(__file__)
DOWNLOADS_DIR = os.path.join(BASE_DIR, 'downloads')
COOKIES_FILE  = os.path.join(BASE_DIR, 'cookies.txt')
YT_COOKIES_FILE = os.path.join(BASE_DIR, 'youtube_cookies.txt')
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

FOLDER_LAYOUT = [
    ('instagram_downloads', os.path.join('instagram', 'downloads')),
    ('instagram_extractions', os.path.join('instagram', 'extractions')),
    ('instagram_cookies', os.path.join('instagram', 'cookies')),
    ('youtube_downloads', os.path.join('youtube', 'downloads')),
    ('youtube_reels', os.path.join('youtube', 'reels')),
    ('youtube_audio_mp3', os.path.join('youtube', 'audio_mp3')),
    ('watermarks', 'watermarks'),
    ('temp_uploads', os.path.join('temp', 'uploads')),
    ('temp_mp3_output', os.path.join('temp', 'mp3_output')),
    ('temp_merged_output', os.path.join('temp', 'merged_output')),
    ('exports_metadata', os.path.join('exports', 'metadata')),
    ('exports_logs', os.path.join('exports', 'logs')),
]

def _is_within_base(path: str) -> bool:
    base = os.path.realpath(BASE_DIR)
    target = os.path.realpath(path)
    return target == base or target.startswith(base + os.sep)

def _resolve_root_dir(value: str = '') -> str:
    candidate = (value or '').strip()
    if not candidate:
        candidate = BASE_DIR
    candidate = os.path.abspath(os.path.normpath(candidate))
    if not _is_within_base(candidate):
        raise ValueError('Root folder must be inside this project directory.')
    return candidate

def _folder_layout(root_dir: str) -> dict:
    return {
        key: os.path.join(root_dir, rel)
        for key, rel in FOLDER_LAYOUT
    }

def _folder_tree_text(root_dir: str) -> str:
    lines = [root_dir + os.sep]
    branches = []
    for _, rel in FOLDER_LAYOUT:
        parts = rel.split(os.sep)
        cur = []
        for p in parts:
            cur.append(p)
            branches.append(tuple(cur))
    uniq = sorted(set(branches))
    for idx, parts in enumerate(uniq):
        prefix = '└─ ' if idx == len(uniq) - 1 else '├─ '
        lines.append(prefix + '/'.join(parts) + '/')
    return '\n'.join(lines)

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
    # Coerce NULL DB values to '' so callers can safely call .strip() on any value
    return {r['key']: (r['value'] or '') for r in rows}

@app.route('/download')
def download_page():
    if not session.get('logged_in'): return redirect(url_for('login'))
    return render_template('download.html', system=system_status(), settings=_get_settings_dict())

@app.route('/metadata')
def metadata_page():
    if not session.get('logged_in'): return redirect(url_for('login'))
    return render_template('metadata.html')

@app.route('/watermark')
def watermark_page():
    if not session.get('logged_in'): return redirect(url_for('login'))
    folders = []
    return render_template('watermark.html', folders=folders, settings=_get_settings_dict())

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

    try:
        root_dir = _resolve_root_dir(settings.get('root_dir', ''))
    except Exception:
        root_dir = BASE_DIR
    defaults = _folder_layout(root_dir)
    settings.setdefault('root_dir', root_dir)
    settings.setdefault('dir_ig', defaults['instagram_downloads'])
    settings.setdefault('dir_yt', defaults['youtube_downloads'])
    settings['folder_tree'] = _folder_tree_text(root_dir)

    # Cookie status

    from modules.account_manager import ensure_default_profiles_exist, get_account_status
    ensure_default_profiles_exist()
    
    accounts = [dict(r) for r in db.execute("SELECT * FROM ig_accounts ORDER BY priority ASC").fetchall()]
    for acc in accounts:
        cookie_path = os.path.join(BASE_DIR, acc['cookie_path']) if acc['cookie_path'] else ''
        acc['has_cookie'] = os.path.isfile(cookie_path)
        if acc['has_cookie']:
             stat = os.stat(cookie_path)
             acc['cookie_size'] = f'{stat.st_size / 1024:.1f} KB'
        else:
             acc['cookie_size'] = ''
        
        # Merge recent limit status
        status = get_account_status(acc['id'])
        acc['used_slots'] = status['used']
        acc['limit'] = status['used'] + status['remaining']
        acc['cooldown'] = status['in_cooldown']

    return render_template('settings.html', settings=settings, accounts=accounts)

@app.route('/instagram')
def instagram_page():
    if not session.get('logged_in'): return redirect(url_for('login'))
    db = get_db()
    cookie_ok   = os.path.isfile(COOKIES_FILE)
    saved_lists = [dict(r) for r in db.execute(
        'SELECT * FROM ig_extractions ORDER BY extracted_at DESC'
    ).fetchall()]
    return render_template('instagram.html', cookie_ok=cookie_ok, saved_lists=saved_lists)

@app.route('/post')
def post_page():
    if not session.get('logged_in'): return redirect(url_for('login'))
    db = get_db()
    accounts = [dict(r) for r in db.execute('SELECT * FROM poster_accounts ORDER BY id').fetchall()]
    return render_template('post.html', accounts=accounts)


@app.route('/create-post')
def create_post_page():
    if not session.get('logged_in'): return redirect(url_for('login'))
    db       = get_db()
    settings = _get_settings_dict()
    accounts = [dict(r) for r in db.execute('SELECT id, label, username, enabled FROM poster_accounts ORDER BY id').fetchall()]
    # YouTube auth status
    from modules.yt_uploader import is_authorized, get_channel_info, YT_CATEGORIES
    yt_authorized  = is_authorized()
    yt_channel     = get_channel_info() if yt_authorized else {}
    yt_cs_saved    = bool(settings.get('yt_client_secret', '').strip())
    ai_configured  = bool(settings.get('openrouter_api_key', '').strip())
    return render_template('create_post.html',
        accounts=accounts,
        yt_authorized=yt_authorized,
        yt_channel=yt_channel,
        yt_cs_saved=yt_cs_saved,
        ai_configured=ai_configured,
        yt_categories=YT_CATEGORIES,
        settings=settings,
    )


# ── YouTube OAuth ─────────────────────────────────────────────────
@app.route('/youtube/oauth/start')
def youtube_oauth_start():
    if not session.get('logged_in'): return redirect(url_for('login'))
    from modules.yt_uploader import get_auth_url
    cs_json = _get_settings_dict().get('yt_client_secret', '')
    if not cs_json:
        flash('Paste your YouTube client_secret.json in Settings → YouTube OAuth first.', 'danger')
        return redirect(url_for('create_post_page'))
    redirect_uri = request.host_url.rstrip('/') + '/youtube/oauth/callback'
    try:
        auth_url = get_auth_url(cs_json, redirect_uri)
        return redirect(auth_url)
    except Exception as e:
        flash(f'OAuth error: {e}', 'danger')
        return redirect(url_for('create_post_page'))


@app.route('/youtube/oauth/callback')
def youtube_oauth_callback():
    code  = request.args.get('code')
    state = request.args.get('state')
    if not code:
        flash('OAuth authorization failed — no code returned.', 'danger')
        return redirect(url_for('create_post_page'))
    from modules.yt_uploader import exchange_code
    ok = exchange_code(code, state)
    if ok:
        flash('✅ YouTube authorized successfully!', 'success')
    else:
        flash('❌ Failed to exchange authorization code.', 'danger')
    return redirect(url_for('create_post_page'))


@app.route('/api/youtube/oauth/save-secret', methods=['POST'])
def api_yt_save_secret():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json() or {}
    cs   = data.get('client_secret', '').strip()
    if not cs:
        return jsonify({'error': 'Empty client_secret'}), 400
    try:
        json.loads(cs)  # validate JSON
    except Exception:
        return jsonify({'error': 'Invalid JSON'}), 400
    db = get_db()
    db.execute('INSERT INTO settings (key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
               ('yt_client_secret', cs))
    db.commit()
    return jsonify({'ok': True})


@app.route('/api/youtube/oauth/revoke', methods=['POST'])
def api_yt_revoke():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    db = get_db()
    db.execute("DELETE FROM settings WHERE key IN ('yt_oauth_token','yt_oauth_flow_state','yt_oauth_client_config','yt_oauth_redirect_uri')")
    db.commit()
    return jsonify({'ok': True})


# ── Post Queue API ────────────────────────────────────────────────
@app.route('/api/post-queue', methods=['GET'])
def api_post_queue_list():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    platform = request.args.get('platform', '')
    status   = request.args.get('status', '')
    page     = int(request.args.get('page', 1))
    limit    = int(request.args.get('limit', 25))
    offset   = (page - 1) * limit
    
    db       = get_db()
    
    # Base WHERE clause
    clauses, vals = [], []
    if platform: clauses.append('platform=?'); vals.append(platform)
    if status:   clauses.append('status=?');   vals.append(status)
    where_sql = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
    
    # Get total count
    total_count = db.execute(f'SELECT COUNT(*) FROM post_queue {where_sql}', vals).fetchone()[0]
    total_pages = (total_count + limit - 1) // limit if limit > 0 else 1
    
    q = f'SELECT * FROM post_queue {where_sql} ORDER BY scheduled_at ASC, id DESC LIMIT ? OFFSET ?'
    rows = db.execute(q, vals + [limit, offset]).fetchall()
    
    return jsonify({
        'items': [dict(r) for r in rows],
        'page': page,
        'limit': limit,
        'total_count': total_count,
        'total_pages': total_pages
    })


@app.route('/api/post-queue/add', methods=['POST'])
def api_post_queue_add():
    """Add one or more items to the post queue."""
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data  = request.get_json() or {}
    items = data.get('items', [data])   # accept single or list
    db    = get_db()
    added = []
    skipped_duplicates = 0
    invalid_items = []
    for item in items:
        fp = item.get('file_path', '').strip()
        if not fp:
            continue
        if os.path.isdir(fp):
            invalid_items.append({'file_path': fp, 'error': 'Expected a single video file path, got a folder path'})
            continue
        if not os.path.isfile(fp):
            invalid_items.append({'file_path': fp, 'error': 'File not found'})
            continue
            
        slot = int(item.get('account_slot', 1))
        
        # Deduplication check: ignore if this file_path is already in the queue for this account slot
        exists = db.execute('SELECT 1 FROM post_queue WHERE file_path=? AND account_slot=? LIMIT 1', (fp, slot)).fetchone()
        if exists:
            skipped_duplicates += 1
            continue
            
        cur = db.execute(
            '''INSERT INTO post_queue
               (platform, account_slot, file_path, title, description, tags,
                privacy, category_id, thumbnail_path, scheduled_at, ai_generated)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            (
                item.get('platform', 'instagram'),
                slot,
                fp,
                item.get('title', ''),
                item.get('description', ''),
                item.get('tags', ''),
                item.get('privacy', 'public'),
                item.get('category_id', '22'),
                item.get('thumbnail_path', ''),
                item.get('scheduled_at') or None,
                int(item.get('ai_generated', 0)),
            )
        )
        added.append(cur.lastrowid)
    db.commit()
    if not added and invalid_items:
        return jsonify({'ok': False, 'error': invalid_items[0]['error'], 'invalid_items': invalid_items}), 400
    return jsonify({'ok': True, 'added': added, 'count': len(added), 'skipped_duplicates': skipped_duplicates, 'invalid_items': invalid_items})


@app.route('/api/post-queue/<int:qid>/cancel', methods=['POST'])
def api_post_queue_cancel(qid):
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    db = get_db()
    db.execute("UPDATE post_queue SET status='cancelled' WHERE id=? AND status IN ('pending','error')", (qid,))
    db.commit()
    return jsonify({'ok': True})


@app.route('/api/post-queue/<int:qid>/retry', methods=['POST'])
def api_post_queue_retry(qid):
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    db = get_db()
    db.execute("UPDATE post_queue SET status='pending', note='' WHERE id=? AND status IN ('error','cancelled')", (qid,))
    db.commit()
    return jsonify({'ok': True})


@app.route('/api/post-queue/<int:qid>', methods=['DELETE'])
def api_post_queue_delete(qid):
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    db = get_db()
    db.execute('DELETE FROM post_queue WHERE id=?', (qid,))
    db.commit()
    return jsonify({'ok': True})


# ── Folder scan for bulk upload ────────────────────────────────────
@app.route('/api/post-queue/scan-folder', methods=['POST'])
def api_post_queue_scan():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data   = request.get_json() or {}
    folder = data.get('folder', '').strip()
    if not folder or not os.path.isdir(folder):
        return jsonify({'error': 'Folder not found'}), 404
    VIDEO_EXTS = ('.mp4','.mov','.m4v','.avi','.mkv','.webm','.ts')
    files = sorted([
        f for f in os.listdir(folder)
        if os.path.splitext(f)[1].lower() in VIDEO_EXTS
    ])
    result = []
    for fn in files:
        stem = os.path.splitext(fn)[0]
        result.append({
            'file_path': os.path.join(folder, fn),
            'filename':  fn,
            'title_guess': stem.replace('_',' ').replace('-',' ').title(),
        })
    return jsonify({'files': result, 'count': len(result)})


# ── AI content generation for a given file/title ──────────────────
@app.route('/api/ai/generate-post', methods=['POST'])
def api_ai_generate_post():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data     = request.get_json() or {}
    topic    = data.get('topic', '').strip()
    platform = data.get('platform', 'youtube')
    if not topic:
        return jsonify({'error': 'No topic provided'}), 400
    cfg     = _get_settings_dict()
    api_key = cfg.get('openrouter_api_key', '').strip()
    if not api_key:
        return jsonify({'error': 'OpenRouter API key not configured'}), 400
    niche   = cfg.get('content_niche', '')
    try:
        if platform == 'youtube':
            from modules.ai_generator import generate_youtube_content
            result = generate_youtube_content(topic, api_key, niche, is_short=False)
        else:
            from modules.ai_generator import generate_instagram_content
            result = generate_instagram_content(topic, api_key, niche, 'reel')
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/ai/test-key', methods=['POST'])
def api_ai_test_key():
    """Ultra-cheap connection test: sends Hi with max_tokens=10. Costs ~0 credits."""
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data    = request.get_json() or {}
    api_key = (data.get('api_key') or '').strip() or (_read_setting('openrouter_api_key') or '').strip()
    if not api_key:
        return jsonify({'ok': False, 'error': 'No API key provided'})
    try:
        import requests as _req
        preferred = (_read_setting('openrouter_model') or '').strip()
        candidates = [
            preferred,
            'google/gemini-2.5-pro',
            'google/gemini-2.0-flash-001',
            'openai/gpt-4o-mini',
            'meta-llama/llama-3.1-8b-instruct:free',
        ]
        tried = []

        for model_name in [m for m in candidates if m]:
            tried.append(model_name)
            resp = _req.post(
                'https://openrouter.ai/api/v1/chat/completions',
                headers={
                    'Authorization': f'Bearer {api_key}',
                    'Content-Type': 'application/json',
                    'HTTP-Referer': 'http://localhost:5056',
                    'X-Title': 'Nikethan Reels Toolkit',
                },
                json={
                    'model': model_name,
                    'max_tokens': 10,
                    'temperature': 0.1,
                    'messages': [{'role': 'user', 'content': 'Hi'}],
                },
                timeout=15,
            )
            if resp.status_code == 200:
                data_r = resp.json()
                reply  = (data_r.get('choices', [{}])[0].get('message', {}).get('content') or '').strip()
                model  = data_r.get('model', model_name)
                return jsonify({'ok': True, 'reply': reply, 'model': model, 'tried': tried})
            if resp.status_code == 429:
                return jsonify({'ok': True, 'error': 'Rate limit hit - key appears valid', 'model': model_name, 'tried': tried})
            if resp.status_code in (401, 403):
                return jsonify({'ok': False, 'error': 'Invalid or unauthorized API key', 'model': model_name})
            if resp.status_code in (404, 422):
                continue

        return jsonify({'ok': False, 'error': f'Unable to validate key with available models. Tried: {", ".join(tried)}'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


@app.route('/api/ai/credits', methods=['GET'])
def api_ai_credits():
    """Check OpenRouter credit balance via two methods with graceful fallback."""
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    api_key = (_read_setting('openrouter_api_key') or '').strip()
    if not api_key:
        return jsonify({'ok': False, 'error': 'No API key configured'})
    try:
        import requests as _req
        headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}

        # Method 1: /api/v1/credits (works with Management keys)
        cr = _req.get('https://openrouter.ai/api/v1/credits', headers=headers, timeout=10)
        if cr.status_code == 200:
            d = cr.json()
            used      = float(d.get('total_usage', 0) or 0)
            purchased = float(d.get('total_credits', d.get('total_credits_purchased', 0)) or 0)
            remaining = purchased - used
            return jsonify({'ok': True, 'method': 'credits_api',
                            'total_credits': round(purchased, 6),
                            'used_credits':  round(used, 6),
                            'remaining':     round(remaining, 6)})

        # Method 2: /api/v1/auth/key (any key - returns rate limits + is_free_tier)
        kr = _req.get('https://openrouter.ai/api/v1/auth/key', headers=headers, timeout=10)
        if kr.status_code == 200:
            d = kr.json().get('data', kr.json())
            return jsonify({'ok': True, 'method': 'key_info',
                            'label': d.get('label', 'API Key'),
                            'usage': d.get('usage', 0),
                            'limit': d.get('limit'),
                            'is_free_tier': d.get('is_free_tier', False),
                            'rate_limit': d.get('rate_limit', {}),
                            'note': 'For full balance use a Management API key from openrouter.ai/settings'})

        return jsonify({'ok': False, 'error': f'OpenRouter returned HTTP {cr.status_code}'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


@app.route('/analytics')
def analytics_page():
    if not session.get('logged_in'): return redirect(url_for('login'))
    settings = _get_settings_dict()
    yt_api_configured = bool(settings.get('yt_api_key', '').strip())
    from modules.analytics_db import get_all_stored_channels
    stored_channels = get_all_stored_channels('youtube')
    return render_template('analytics.html', settings=settings,
                           yt_api_configured=yt_api_configured,
                           stored_channels=stored_channels)


@app.route('/api/analytics/youtube', methods=['POST'])
def api_analytics_youtube():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data    = request.get_json() or {}
    channel = data.get('channel', '').strip()
    if not channel: return jsonify({'error': 'No channel provided'}), 400
    api_key = _read_setting('yt_api_key')
    if not api_key: return jsonify({'error': 'YouTube API key not configured. Go to Settings → API Keys.'}), 400
    try:
        from modules.analytics import get_youtube_channel_stats
        from modules.analytics_db import save_snapshot
        result = get_youtube_channel_stats(channel, api_key)
        # Determine if this is the user's "home" channel
        home_ch = (_read_setting('home_channel') or '').strip().lstrip('@').lower()
        ch_handle = result['channel'].get('title', '').lower()
        is_own = bool(home_ch and (home_ch in ch_handle or ch_handle in home_ch or
                                    home_ch == result['channel']['id'].lower()))
        save_snapshot(
            channel_id=result['channel']['id'],
            channel_title=result['channel']['title'],
            data=result,
            is_own_channel=is_own,
            platform='youtube'
        )
        result['is_own_channel'] = is_own
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        app.logger.error(f'YouTube analytics error: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/analytics/multi', methods=['POST'])
def api_analytics_multi():
    """Analyse multiple channels in one call — returns comparison table."""
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json() or {}
    channels = data.get('channels', [])
    if not channels: return jsonify({'error': 'No channels provided'}), 400
    if len(channels) > 10: return jsonify({'error': 'Maximum 10 channels per comparison'}), 400
    api_key = _read_setting('yt_api_key')
    if not api_key: return jsonify({'error': 'YouTube API key not configured.'}), 400
    try:
        from modules.analytics import get_multi_channel_comparison
        from modules.analytics_db import save_snapshot
        results = get_multi_channel_comparison(channels, api_key)
        home_ch = (_read_setting('home_channel') or '').strip().lstrip('@').lower()
        for r in results:
            if r.get('error') or not r.get('full_data'):
                continue
            is_own = bool(home_ch and home_ch in r.get('title', '').lower())
            save_snapshot(
                channel_id=r['channel_id'],
                channel_title=r['title'],
                data=r['full_data'],
                is_own_channel=is_own,
                platform='youtube'
            )
            del r['full_data']  # Don't send full data back for comparison (too large)
        return jsonify({'results': results})
    except Exception as e:
        app.logger.error(f'Multi-channel analytics error: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/analytics/snapshots/<channel_id>')
def api_analytics_snapshots(channel_id):
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    from modules.analytics_db import get_snapshots
    snaps = get_snapshots(channel_id, 'youtube')
    # Strip full video list from snapshots for speed (return summary only)
    for s in snaps:
        if 'data' in s and 'videos' in s['data']:
            s['data']['videos'] = s['data']['videos'][:5]  # Keep top 5 only for history view
    return jsonify({'snapshots': snaps})


@app.route('/api/analytics/video', methods=['POST'])
def api_analytics_video():
    """Single video deep-dive analysis."""
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json() or {}
    video_input = data.get('video', '').strip()
    channel_avg = data.get('channel_avg')  # optional, passed from frontend
    if not video_input: return jsonify({'error': 'No video URL or ID provided'}), 400
    api_key = _read_setting('yt_api_key')
    if not api_key: return jsonify({'error': 'YouTube API key not configured.'}), 400
    try:
        from modules.analytics import get_youtube_video_deep_analysis
        result = get_youtube_video_deep_analysis(video_input, api_key, channel_avg)
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        app.logger.error(f'Video analytics error: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/analytics/trending', methods=['POST'])
def api_analytics_trending():
    """YouTube trending videos for day/week/month split by Shorts vs Long."""
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json() or {}
    window      = data.get('window', 'day')
    region_code = data.get('region', 'US').upper()
    category_id = data.get('category', '0')
    api_key = _read_setting('yt_api_key')
    if not api_key: return jsonify({'error': 'YouTube API key not configured.'}), 400
    try:
        from modules.analytics import get_youtube_trending
        result = get_youtube_trending(api_key, region_code, category_id, window)
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        app.logger.error(f'Trending analytics error: {e}')
        return jsonify({'error': str(e)}), 500



@app.route('/api/analytics/instagram', methods=['POST'])
def api_analytics_instagram():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json() or {}
    username = data.get('username', '').strip().lstrip('@')
    if not username: return jsonify({'error': 'No username provided'}), 400
    try:
        from modules.analytics import get_instagram_profile_stats
        from modules.analytics_db import save_snapshot
        cookie_path = COOKIES_FILE if os.path.isfile(COOKIES_FILE) else ''
        result = get_instagram_profile_stats(username, cookie_path)
        save_snapshot(username, username, result, is_own_channel=False, platform='instagram')
        return jsonify(result)
    except Exception as e:
        app.logger.error(f'Instagram analytics error: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/settings/keys/save', methods=['POST'])
def api_save_keys():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json() or {}
    db = get_db()
    allowed = ['yt_api_key', 'home_channel', 'openrouter_api_key', 'content_niche',
               'openrouter_model', 'ai_language', 'ai_model']
    for k in allowed:
        if k in data:
            db.execute('INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (k, data[k]))
    db.commit()
    return jsonify({'ok': True})

# ══════════════════════════════════════════════════════════════
#  API — System Dependencies
# ══════════════════════════════════════════════════════════════

@app.route('/api/system/status')
def api_system_status():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    from modules.dependency_manager import check_system_status
    return jsonify(check_system_status())

@app.route('/api/system/update-ffmpeg', methods=['POST'])
def api_system_update_ffmpeg():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {'events': [], 'done': False}
    
    def _cb(ev_type, msg):
        JOBS[job_id]['events'].append({'type': ev_type, 'msg': msg, 'ts': datetime.utcnow().isoformat()})
        if ev_type == 'done':
            JOBS[job_id]['done'] = True
            
    threading.Thread(target=install_ffmpeg_thread, args=(_cb,), daemon=True).start()
    return jsonify({'ok': True, 'job_id': job_id})

@app.route('/api/system/update-ytdlp', methods=['POST'])
def api_system_update_ytdlp():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {'events': [], 'done': False}
    
    def _cb(ev_type, msg):
        JOBS[job_id]['events'].append({'type': ev_type, 'msg': msg, 'ts': datetime.utcnow().isoformat()})
        if ev_type == 'done':
            JOBS[job_id]['done'] = True
            
    threading.Thread(target=upgrade_ytdlp_thread, args=(_cb,), daemon=True).start()
    return jsonify({'ok': True, 'job_id': job_id})




def _append_setup_event(job, step, ok, detail):
    job['events'].append({
        'ts': datetime.utcnow().isoformat(),
        'step': step,
        'ok': bool(ok),
        'detail': (detail or '')[:600],
    })


def _run_initial_setup_job(job_id):
    import platform
    job = JOBS.get(job_id)
    if not job:
        return

    def run_step(step_name, fn, critical=True):
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, str(e)
        _append_setup_event(job, step_name, ok, detail)
        job['summary'].append({'step': step_name, 'ok': bool(ok), 'critical': bool(critical), 'detail': (detail or '')[:600]})
        if critical and not ok:
            job['ok'] = False
        return ok

    job['ok'] = True

    def step_requirements():
        req = os.path.join(BASE_DIR, 'requirements.txt')
        if not os.path.isfile(req):
            return False, 'requirements.txt not found'
        cmd = [sys.executable, '-m', 'pip', 'install', '-r', req]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
        if r.returncode == 0:
            return True, 'Python requirements installed/verified'
        err = (r.stderr or r.stdout or '').strip()[-350:]
        return False, f'pip install -r requirements.txt failed: {err}'

    def step_instagrapi():
        try:
            import instagrapi  # noqa: F401
            return True, 'instagrapi already installed'
        except Exception:
            pass
        cmd = [sys.executable, '-m', 'pip', 'install', 'instagrapi']
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if r.returncode == 0:
            return True, 'instagrapi installed'
        err = (r.stderr or r.stdout or '').strip()[-350:]
        return False, f'instagrapi install failed: {err}'

    def step_ffmpeg():
        if shutil.which('ffmpeg'):
            return True, f'ffmpeg detected in PATH: {shutil.which("ffmpeg")}'
        if platform.system().lower().startswith('win'):
            msgs = []
            def _cb(ev_type, msg):
                if ev_type in ('status', 'error') and msg:
                    msgs.append(msg)
            install_ffmpeg_thread(_cb)
            if shutil.which('ffmpeg'):
                return True, 'FFmpeg installed locally in _dependencies and added to PATH'
            return False, ('; '.join(msgs) or 'FFmpeg install attempted but ffmpeg still not detected')[:500]
        return False, 'FFmpeg missing. Install via OS package manager (brew/apt/dnf) or place in PATH.'

    def step_ytdlp():
        cmd = [sys.executable, '-m', 'pip', 'install', '--upgrade', 'yt-dlp']
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if r.returncode == 0:
            return True, 'yt-dlp upgraded/installed successfully'
        err = (r.stderr or r.stdout or '').strip()[-350:]
        return False, f'yt-dlp install/upgrade failed: {err}'

    def step_healthcheck():
        status = check_system_status()
        ff_ok = bool((status.get('ffmpeg') or {}).get('installed'))
        y_ok = bool((status.get('ytdlp') or {}).get('installed'))
        detail = f"ffmpeg={ff_ok}, ytdlp={y_ok}, cuda={(status.get('cuda') or {}).get('available', False)}"
        return (ff_ok and y_ok), detail

    run_step('Python requirements', step_requirements, critical=True)
    run_step('Instagram dependency (instagrapi)', step_instagrapi, critical=False)
    run_step('FFmpeg availability', step_ffmpeg, critical=True)
    run_step('yt-dlp availability', step_ytdlp, critical=True)
    run_step('System health check', step_healthcheck, critical=True)

    if not any(s.get('critical') and not s.get('ok') for s in job['summary']):
        job['ok'] = True
    job['done'] = True


@app.route('/api/system/initial-setup/start', methods=['POST'])
def api_system_initial_setup_start():
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {'events': [], 'done': False, 'ok': False, 'summary': []}
    threading.Thread(target=_run_initial_setup_job, args=(job_id,), daemon=True).start()
    return jsonify({'ok': True, 'job_id': job_id})


@app.route('/api/system/initial-setup/status/<job_id>', methods=['GET'])
def api_system_initial_setup_status(job_id):
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401
    job = JOBS.get(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify({
        'ok': bool(job.get('ok')),
        'done': bool(job.get('done')),
        'events': job.get('events', []),
        'summary': job.get('summary', []),
    })

@app.route('/api/system/test-infra')
def api_test_infra():
    """Deep live test: ffmpeg encode, GPU, yt-dlp version, Python encoding."""
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    import shutil, sys, tempfile as _tf
    results = []

    # ── 1. ffmpeg present ──────────────────────────────────────
    ffmpeg_path = shutil.which('ffmpeg')
    if ffmpeg_path:
        try:
            ver_out = subprocess.check_output(
                ['ffmpeg', '-version'], stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace'
            )
            ver_line = ver_out.splitlines()[0]
            results.append({'name': 'FFmpeg Binary', 'ok': True,
                            'detail': ver_line, 'path': ffmpeg_path})
        except Exception as e:
            results.append({'name': 'FFmpeg Binary', 'ok': False, 'detail': str(e)})
    else:
        results.append({'name': 'FFmpeg Binary', 'ok': False,
                        'detail': 'ffmpeg not found in PATH'})

    # ── 2. GPU / NVENC ─────────────────────────────────────────
    try:
        enc_out = subprocess.check_output(
            ['ffmpeg', '-encoders'], stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace'
        )
        if 'h264_nvenc' in enc_out:
            from modules.cuda_check import detect_cuda
            cuda = detect_cuda()
            results.append({'name': 'GPU / NVENC', 'ok': True,
                            'detail': f'h264_nvenc available — device: {cuda.get("device", "unknown")}'})
        else:
            results.append({'name': 'GPU / NVENC', 'ok': False,
                            'detail': 'h264_nvenc encoder NOT listed — GPU encoding unavailable; will fallback to CPU'})
    except Exception as e:
        results.append({'name': 'GPU / NVENC', 'ok': False, 'detail': str(e)})

    # ── 3. FFmpeg encode smoke-test (1s black clip) ────────────
    try:
        with _tf.TemporaryDirectory() as tmpdir:
            out = os.path.join(tmpdir, 'test.mp4')
            cmd = ['ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=black:s=160x90:r=1',
                   '-t', '1', '-c:v', 'libx264', '-preset', 'ultrafast', out]
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding='utf-8', errors='replace', timeout=30)
            if r.returncode == 0 and os.path.isfile(out):
                results.append({'name': 'FFmpeg Encode Test (CPU)', 'ok': True,
                                'detail': 'Encoded a 1-second test clip successfully'})
            else:
                results.append({'name': 'FFmpeg Encode Test (CPU)', 'ok': False,
                                'detail': r.stderr[-300:] if r.stderr else 'No output file created'})
    except Exception as e:
        results.append({'name': 'FFmpeg Encode Test (CPU)', 'ok': False, 'detail': str(e)})

    # ── 4. yt-dlp present ─────────────────────────────────────
    try:
        ver = subprocess.check_output(
            ['yt-dlp', '--version'], stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', errors='replace', timeout=10
        ).strip()
        results.append({'name': 'yt-dlp Binary', 'ok': True, 'detail': f'Version {ver}'})
    except Exception as e:
        results.append({'name': 'yt-dlp Binary', 'ok': False, 'detail': str(e)})

    # ── 5. Python locale / encoding ───────────────────────────
    import locale
    fs_enc   = sys.getfilesystemencoding()
    std_enc  = sys.stdout.encoding or 'unknown'
    loc_info = locale.getpreferredencoding(False)
    safe = (fs_enc.lower() == 'utf-8' and 'utf' in std_enc.lower())
    results.append({
        'name': 'Python Locale (UTF-8)',
        'ok': safe,
        'detail': (f'filesystem={fs_enc}  stdout={std_enc}  locale={loc_info}'
                   + ('' if safe else ' — ⚠️ Non-UTF8 locale can cause UnicodeDecodeError with special filenames'))
    })

    all_ok = all(r['ok'] for r in results)
    return jsonify({'ok': all_ok, 'results': results})


# ── AI Content Studio ──────────────────────────────────────────

@app.route('/ai-content')
def ai_content_page():
    if not session.get('logged_in'): return redirect(url_for('login'))
    settings = _get_settings_dict()
    ai_configured = bool(settings.get('openrouter_api_key', '').strip())
    return render_template('ai_content.html', settings=settings, ai_configured=ai_configured)


@app.route('/api/ai/youtube', methods=['POST'])
def api_ai_youtube():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data      = request.get_json() or {}
    topic     = data.get('topic', '').strip()
    is_short  = bool(data.get('is_short', False))
    if not topic: return jsonify({'error': 'No topic provided'}), 400
    cfg     = _get_settings_dict()
    api_key = cfg.get('openrouter_api_key', '').strip()
    if not api_key: return jsonify({'error': 'OpenRouter API key not configured. Go to Settings → API Keys.'}), 400
    niche    = cfg.get('content_niche', '')
    language = cfg.get('ai_language', 'english')
    try:
        from modules.ai_generator import generate_youtube_content
        result = generate_youtube_content(topic, api_key, niche, is_short, language=language)
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        app.logger.error(f'AI YouTube error: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/ai/instagram', methods=['POST'])
def api_ai_instagram():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data         = request.get_json() or {}
    topic        = data.get('topic', '').strip()
    content_type = data.get('content_type', 'reel')
    if not topic: return jsonify({'error': 'No topic provided'}), 400
    cfg     = _get_settings_dict()
    api_key = cfg.get('openrouter_api_key', '').strip()
    if not api_key: return jsonify({'error': 'OpenRouter API key not configured. Go to Settings → API Keys.'}), 400
    niche    = cfg.get('content_niche', '')
    language = cfg.get('ai_language', 'english')
    try:
        from modules.ai_generator import generate_instagram_content
        result = generate_instagram_content(topic, api_key, niche, content_type, language=language)
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        app.logger.error(f'AI Instagram error: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/ai/expand-tags', methods=['POST'])
def api_ai_expand_tags():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data      = request.get_json() or {}
    seed_tags = data.get('tags', [])
    if not seed_tags: return jsonify({'error': 'No seed tags provided'}), 400
    cfg     = _get_settings_dict()
    api_key = cfg.get('openrouter_api_key', '').strip()
    if not api_key: return jsonify({'error': 'OpenRouter API key not configured.'}), 400
    niche = cfg.get('content_niche', '')
    try:
        from modules.ai_generator import expand_tags
        result = expand_tags(seed_tags, api_key, niche)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


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

def _safe_remove_source(path: str, generated_paths=None) -> tuple[bool, str]:
    """Delete source file only after successful generated outputs exist."""
    try:
        if not path:
            return False, 'No source path provided'
        real_src = os.path.realpath(path)
        if not os.path.isfile(real_src):
            return False, 'Source file already missing'
        outputs = []
        for p in (generated_paths or []):
            if p and os.path.isfile(p):
                outputs.append(os.path.realpath(p))
        if not outputs:
            return False, 'No generated outputs found; source retained'
        if any(op == real_src for op in outputs):
            return False, 'Generated output matches source; source retained'
        os.remove(real_src)
        return True, f'Deleted source file: {os.path.basename(real_src)}'
    except Exception as e:
        return False, f'Could not delete source file: {e}'

@app.route('/api/download', methods=['POST'])
def api_download():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data    = request.get_json()
    urls    = data.get('urls', [])
    quality = data.get('quality', 'best')
    out_dir = data.get('output_dir', '').strip()
    opt_watermark = data.get('opt_watermark', False)
    opt_split     = data.get('opt_split', False)
    opt_parts     = data.get('opt_parts', False)
    delete_source = bool(data.get('delete_source', False))
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

        # ── Pipeline: watermark / split / part-stamp ──────────
        if any([opt_watermark, opt_split, opt_parts]):
            from modules.pipeline import run_pipeline
            with app.app_context():
                settings = _get_settings_dict()
            pipeline_results = []
            for r in results:
                fp = r.get('file_path') or r.get('path', '')
                if fp and os.path.isfile(fp):
                    _emit(job, f'▶ Post-processing: {os.path.basename(fp)}')
                    final_files = run_pipeline(
                        file_path=fp,
                        settings=settings,
                        opt_watermark=opt_watermark,
                        opt_split=opt_split,
                        opt_parts=opt_parts,
                        progress_cb=lambda line, pct=None: _emit(job, line, pct)
                    )
                    for ff in final_files:
                        pipeline_results.append({'status': 'ok', 'path': ff, 'file_path': ff})
                    if delete_source:
                        deleted, msg = _safe_remove_source(fp, final_files)
                        _emit(job, ('🧹 ' if deleted else '⚠ ') + msg)
            results = pipeline_results if pipeline_results else results

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
            datetime.now(_dt_module.UTC).isoformat(),
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

@app.route('/api/utils/open-folder', methods=['POST'])
def api_open_folder():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json() or {}
    folder = (data.get('path') or '').strip()
    if not folder:
        return jsonify({'error': 'No folder path provided'}), 400
    folder = os.path.abspath(os.path.normpath(folder))
    if not os.path.isdir(folder):
        return jsonify({'error': 'Folder not found'}), 404

    try:
        if sys.platform.startswith('win'):
            subprocess.Popen(['explorer', folder])
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', folder])
        else:
            subprocess.Popen(['xdg-open', folder])
        return jsonify({'ok': True, 'opened_path': folder})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.route('/api/utils/scan-folder')
def api_scan_folder():
    """Count video files in a given folder path (for watermark / bulk ops)."""
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    folder = (request.args.get('path') or '').strip()
    if not folder or not os.path.isdir(folder):
        return jsonify({'error': 'Invalid or missing folder path', 'count': 0, 'files': []}), 400
    video_exts = {'.mp4', '.mov', '.mkv', '.webm', '.m4v', '.avi'}
    files = [
        f for f in os.listdir(folder)
        if os.path.isfile(os.path.join(folder, f))
        and os.path.splitext(f)[1].lower() in video_exts
    ]
    files.sort()
    return jsonify({'count': len(files), 'files': files, 'folder': folder})


@app.route('/api/utils/pick-file')
def api_pick_file():
    """Native file picker for selecting a single file path."""
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    filetypes = request.args.get('filetypes', 'video').strip().lower()
    if filetypes == 'image':
        tk_types = [('Image files', '*.jpg *.jpeg *.png *.webp *.bmp'), ('All files', '*.*')]
    else:
        tk_types = [('Video files', '*.mp4 *.mov *.mkv *.webm *.m4v *.avi'), ('All files', '*.*')]
    try:
        code = (
            "import tkinter as tk, tkinter.filedialog as fd;"
            "root=tk.Tk(); root.attributes('-topmost', True); root.withdraw();"
            f"print(fd.askopenfilename(parent=root, title='Select File', filetypes={repr(tk_types)}))"
        )
        out = subprocess.check_output([sys.executable, '-c', code], text=True).strip()
        if out == "None" or not out: out = ""
        return jsonify({'file': out})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/utils/test-gpu')
@app.route('/api/utils/browse-folder', methods=['GET'])
def api_browse_folder():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    
    # Run tkinter folder picker in a separate process to avoid thread-blocking
    import subprocess, sys
    script = "import tkinter as tk; from tkinter import filedialog; root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True); print(filedialog.askdirectory())"
    try:
        res = subprocess.check_output([sys.executable, '-c', script], text=True).strip()
        return jsonify({'path': res})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def api_test_gpu():
    global CUDA_INFO
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    import modules.cuda_check
    modules.cuda_check._cache = None
    res = modules.cuda_check.detect_cuda()
    CUDA_INFO = res
    return jsonify(res)

# ══════════════════════════════════════════════════════════════
#  API — Metadata
# ══════════════════════════════════════════════════════════════
@app.route('/api/metadata/list', methods=['GET'])
def api_metadata_list():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    
    page    = int(request.args.get('page', 1))
    limit   = int(request.args.get('limit', 24))
    account = request.args.get('account', '')
    no_tags = request.args.get('no_tags', 'false') == 'true'
    no_desc = request.args.get('no_desc', 'false') == 'true'
    search  = request.args.get('search', '').strip().lower()
    
    db = get_db()
    clauses, vals = [], []
    
    if account:
        clauses.append("account=?")
        vals.append(account)
    if no_tags:
        clauses.append("(tags IS NULL OR tags='' OR tags='[]')")
    if no_desc:
        clauses.append("(caption IS NULL OR caption='')")
    
    where_sql = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
    
    total_count = db.execute(f"SELECT COUNT(*) FROM reels {where_sql}", vals).fetchone()[0]
    total_pages = (total_count + limit - 1) // limit if limit > 0 else 1
    
    q = f"SELECT * FROM reels {where_sql} ORDER BY downloaded_at DESC"
    # if not doing manual search filter in SQL, we just grab all and filter down below for memory if needed, but here we do pagination:
    # Actually, if we have a text search, do it in SQL:
    if search:
        clauses.append("(LOWER(title) LIKE ? OR LOWER(caption) LIKE ? OR LOWER(tags) LIKE ?)")
        search_term = f"%{search}%"
        vals.extend([search_term, search_term, search_term])
        where_sql = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
        total_count = db.execute(f"SELECT COUNT(*) FROM reels {where_sql}", vals).fetchone()[0]
        total_pages = (total_count + limit - 1) // limit if limit > 0 else 1
        q = f"SELECT * FROM reels {where_sql} ORDER BY downloaded_at DESC"

    q += " LIMIT ? OFFSET ?"
    rows = db.execute(q, vals + [limit, (page - 1) * limit]).fetchall()
    
    # get distinct accounts for filter dropdown
    accounts = [r[0] for r in db.execute("SELECT DISTINCT account FROM reels WHERE account IS NOT NULL").fetchall() if r[0]]
    
    return jsonify({
        'items': [dict(r) for r in rows],
        'page': page,
        'limit': limit,
        'total_count': total_count,
        'total_pages': total_pages,
        'accounts': accounts
    })


@app.route('/api/metadata/batch-action', methods=['POST'])
def api_metadata_batch_action():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data   = request.get_json() or {}
    action = data.get('action') # "delete" or "queue"
    ids    = data.get('ids', [])
    
    if not ids or not action:
        return jsonify({'error': 'Missing ids or action'}), 400
        
    db = get_db()
    
    if action == 'delete':
        placeholders = ','.join(['?']*len(ids))
        db.execute(f"DELETE FROM reels WHERE id IN ({placeholders})", ids)
        db.commit()
        return jsonify({'ok': True})
        
    if action == 'queue':
        placeholders = ','.join(['?']*len(ids))
        reels = [dict(r) for r in db.execute(f"SELECT * FROM reels WHERE id IN ({placeholders})", ids).fetchall()]
        
        convert  = data.get('convert', False)
        platform = data.get('platform', 'instagram')
        slot     = int(data.get('account_slot', 1))
        # Custom scheduling
        gap_mins = int(data.get('gap_mins', 120))
        start_dt = data.get('start_time') # iso format
        apply_wm = data.get('apply_watermark', False)
        
        watermark_text = _read_setting('watermark_text') if apply_wm else ''
        
        # We need a background thread for conversion since FFmpeg takes time
        def _bg_process(reels_data, db_path, platform, slot, start_dt, gap_mins, convert, watermark_text):
            from datetime import datetime, timedelta
            import threading, sqlite3
            con = sqlite3.connect(db_path)
            
            try:
                base_time = datetime.fromisoformat(start_dt) if start_dt else datetime.utcnow()
                
                if convert: 
                    from modules.reel_converter import convert_to_reels
                    import re
                    
                for idx, r in enumerate(reels_data):
                    fp = r['file_path']
                    if not fp or not os.path.isfile(fp): continue
                    
                    target_time = base_time + timedelta(minutes=gap_mins * idx)
                    
                    # Dedup check
                    exists = con.execute('SELECT 1 FROM post_queue WHERE file_path=? AND account_slot=? LIMIT 1', (fp, slot)).fetchone()
                    
                    if not convert:
                        if exists: continue
                        con.execute(
                            '''INSERT INTO post_queue
                               (platform, account_slot, file_path, title, description, tags, privacy, scheduled_at, ai_generated)
                               VALUES (?,?,?,?,?,?,?,?,0)''',
                            (platform, slot, fp, r['title'] or '', r['caption'] or '', r['tags'] or '', 'public', target_time.isoformat())
                        )
                    else:
                        # Convert!
                        safe_title = re.sub(r'[^\w\s-]', '', r['title'] or r['id']).strip().replace(' ', '_')
                        if not safe_title: safe_title = r['id']
                        
                        out_dir = os.path.join(os.path.dirname(fp), safe_title)
                        
                        res = convert_to_reels(
                            input_path=fp,
                            output_dir=out_dir,
                            title=r['title'] or '',
                            watermark=watermark_text,
                            part_duration_sec=60,
                            show_title=True,
                            show_part_label=True,
                            show_watermark=bool(watermark_text)
                        )
                        
                        for part_idx, part_fp in enumerate(res.get('parts', [])):
                            p_time = target_time + timedelta(minutes=gap_mins * part_idx)
                            if con.execute('SELECT 1 FROM post_queue WHERE file_path=? AND account_slot=? LIMIT 1', (part_fp, slot)).fetchone():
                                continue
                            con.execute(
                                '''INSERT INTO post_queue
                                   (platform, account_slot, file_path, title, description, tags, privacy, scheduled_at, ai_generated)
                                   VALUES (?,?,?,?,?,?,?,?,0)''',
                                (platform, slot, part_fp, f"{r['title']} Part {part_idx+1}", r['caption'] or '', r['tags'] or '', 'public', p_time.isoformat())
                            )
                            
                con.commit()
            except Exception as e:
                app.logger.error(f'Batch convert/queue error: {e}')
            finally:
                con.close()
                
        import threading
        t = threading.Thread(target=_bg_process, args=(reels, DB_PATH, platform, slot, start_dt, gap_mins, convert, watermark_text))
        t.daemon = True
        t.start()
        
        return jsonify({'ok': True, 'msg': 'Batch processing started in background'})

@app.route('/api/metadata/<reel_id>', methods=['GET'])
def api_metadata_single(reel_id):
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    db  = get_db()
    row = db.execute('SELECT * FROM reels WHERE id=?', (reel_id,)).fetchone()
    if not row: abort(404)
    return jsonify(dict(row))

@app.route('/api/metadata/<reel_id>/stream', methods=['GET'])
def api_metadata_stream(reel_id):
    """Secure video stream endpoint for metadata preview modal."""
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    db  = get_db()
    row = db.execute('SELECT file_path, title FROM reels WHERE id=?', (reel_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Reel not found'}), 404

    fpath = (row['file_path'] or '').strip()

    # Primary path missing — try fallback: scan configured download folders
    if not fpath or not os.path.isfile(fpath):
        filename = os.path.basename(fpath) if fpath else ''
        found = None
        # Scan all known output folders for a matching filename
        cfg = _get_settings_dict()
        search_roots = []
        try:
            from modules.downloader import OUTPUT_DIRS
            search_roots = list(OUTPUT_DIRS.values())
        except Exception:
            pass
        # Also scan default BASE_DIR sub-folders
        for sub in ['downloads', 'output', 'reels', 'tmp_uploads']:
            p = os.path.join(BASE_DIR, sub)
            if os.path.isdir(p):
                search_roots.append(p)
        for root in search_roots:
            if not root or not os.path.isdir(root):
                continue
            for dirpath, _, files in os.walk(root):
                for fn in files:
                    if filename and fn == filename:
                        found = os.path.join(dirpath, fn)
                        break
                    # Fallback: match reel_id in filename
                    if reel_id in fn and fn.lower().endswith(('.mp4','.mov','.mkv','.webm','.m4v')):
                        found = os.path.join(dirpath, fn)
                        break
                if found:
                    break
            if found:
                break
        if found:
            # Update DB so next request succeeds without scanning
            db.execute('UPDATE reels SET file_path=? WHERE id=?', (found, reel_id))
            db.commit()
            fpath = found
        else:
            return jsonify({'error': f'Video file not found on disk. Stored path: {fpath}'}), 404

    ext = os.path.splitext(fpath)[1].lower()
    if ext not in ('.mp4', '.mov', '.mkv', '.webm', '.m4v'):
        return jsonify({'error': f'Unsupported video type: {ext}'}), 415

    mime_map = {
        '.mp4': 'video/mp4',
        '.mov': 'video/quicktime',
        '.mkv': 'video/x-matroska',
        '.webm': 'video/webm',
        '.m4v': 'video/x-m4v',
    }
    return send_file(fpath, mimetype=mime_map.get(ext, 'application/octet-stream'), conditional=True)

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
    mode         = request.form.get('mode', 'upload')   # upload | instagram | text
    wm_type_req  = request.form.get('wm_type', '')      # 'text' forces text watermark

    if not folder or not os.path.isdir(folder):
        return jsonify({'error': 'Invalid folder'}), 400

    # ── Text Watermark Mode ────────────────────────────────────────────────
    if mode == 'text' or wm_type_req == 'text':
        cfg = _get_settings()
        wm_text     = request.form.get('wm_text', '').strip() or cfg.get('wm_text', '@nikethan')
        wm_font     = cfg.get('wm_font', 'Calibri')
        wm_fontsize = int(cfg.get('wm_fontsize', 24))
        wm_color    = cfg.get('wm_color', 'white')
        wm_opacity  = float(cfg.get('wm_opacity', 85)) / 100

        job_id = str(uuid.uuid4())
        job    = _make_job(job_id)
        def run_text():
            count = apply_watermark_to_folder(
                folder=folder, watermark_path=None,
                position=position, opacity=wm_opacity, scale=scale,
                output_mode=output_mode,
                wm_type='text', wm_text=wm_text,
                wm_font=wm_font, wm_fontsize=wm_fontsize, wm_color=wm_color,
                progress_cb=lambda line, pct=None: _emit(job, line, pct),
            )
            _finish(job, f'Watermarked {count} files (text)', count=count)
        threading.Thread(target=run_text, daemon=True).start()
        return jsonify({'job_id': job_id})

    # ── Image Watermark Mode ───────────────────────────────────────────────
    # Handle file upload
    if mode == 'upload' and 'watermark_file' in request.files:
        wm_file = request.files['watermark_file']
        wm_dir  = os.path.join(DOWNLOADS_DIR, 'watermarks')
        os.makedirs(wm_dir, exist_ok=True)
        wm_dest = os.path.join(wm_dir, f'__upload_{uuid.uuid4().hex[:8]}__' + os.path.splitext(wm_file.filename)[1])
        wm_file.save(wm_dest)
        watermark_path = wm_dest

    if not watermark_path or not os.path.isfile(watermark_path):
        return jsonify({'error': 'No valid watermark image. Choose an image file, Instagram fetch, or switch to Text mode.'}), 400

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
    data = request.get_json() or {}
    root_raw = data.get('root_dir', '').strip()
    if root_raw:
        try:
            root_dir = _resolve_root_dir(root_raw)
        except Exception as exc:
            return jsonify({'error': str(exc)}), 400
        _save_setting('root_dir', root_dir)
    
    # Allow individual updates
    if 'dir_ig' in data: _save_setting('dir_ig', data.get('dir_ig', '').strip())
    if 'dir_yt' in data: _save_setting('dir_yt', data.get('dir_yt', '').strip())
    
    return jsonify({'ok': True})

@app.route('/api/settings/directories/reset', methods=['POST'])
def api_directories_reset():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    cfg = _get_settings_dict()
    try:
        root_dir = _resolve_root_dir(cfg.get('root_dir', ''))
    except Exception:
        root_dir = BASE_DIR

    layout = _folder_layout(root_dir)
    _save_setting('root_dir', root_dir)
    _save_setting('dir_ig', layout['instagram_downloads'])
    _save_setting('dir_yt', layout['youtube_downloads'])

    return jsonify({
        'ok': True,
        'root_dir': root_dir,
        'effective_dirs': {
            'dir_ig': layout['instagram_downloads'],
            'dir_yt': layout['youtube_downloads'],
        },
        'tree': _folder_tree_text(root_dir),
    })

@app.route('/api/settings/root/setup', methods=['POST'])
def api_settings_root_setup():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json() or {}
    try:
        root_dir = _resolve_root_dir((data.get('root_dir') or '').strip())
    except Exception as exc:
        return jsonify({'error': str(exc)}), 400

    layout = _folder_layout(root_dir)
    created, existed = [], []
    for p in layout.values():
        if os.path.isdir(p):
            existed.append(p)
        else:
            os.makedirs(p, exist_ok=True)
            created.append(p)

    _save_setting('root_dir', root_dir)
    _save_setting('dir_ig', layout['instagram_downloads'])
    _save_setting('dir_yt', layout['youtube_downloads'])

    return jsonify({
        'ok': True,
        'root_dir': root_dir,
        'created_paths': created,
        'existing_paths': existed,
        'effective_dirs': {
            'dir_ig': layout['instagram_downloads'],
            'dir_yt': layout['youtube_downloads'],
            'watermarks': layout['watermarks'],
            'cookies': layout['instagram_cookies'],
            'temp_uploads': layout['temp_uploads'],
            'instagram_extractions': layout['instagram_extractions'],
            'youtube_audio_mp3': layout['youtube_audio_mp3'],
        },
        'tree': _folder_tree_text(root_dir),
    })

@app.route('/api/settings/root/tree')
def api_settings_root_tree():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    cfg = _get_settings_dict()
    try:
        root_dir = _resolve_root_dir(cfg.get('root_dir', ''))
    except Exception:
        root_dir = BASE_DIR
    return jsonify({'ok': True, 'root_dir': root_dir, 'tree': _folder_tree_text(root_dir)})

@app.route('/api/settings/cookies/save', methods=['POST'])
def api_cookies_save():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data    = request.get_json()
    account_id = data.get('account_id', 'p1')
    content = (data.get('content') or '').strip()
    if not content: return jsonify({'error': 'Empty content'}), 400
    
    lines = [l for l in content.splitlines() if l.strip() and not l.startswith('#')]
    cookie_path = os.path.join('downloads', 'cookies', f'{account_id}.txt')
    abs_cookie_path = os.path.join(BASE_DIR, cookie_path)
    os.makedirs(os.path.dirname(abs_cookie_path), exist_ok=True)
    
    with open(abs_cookie_path, 'w', encoding='utf-8') as f:
        f.write(content)
        
    db = get_db()
    db.execute("UPDATE ig_accounts SET cookie_path=? WHERE id=?", (cookie_path, account_id))
    db.commit()

    # Warn if sessionid is missing (required for authenticated Instagram downloads)
    has_sessionid = any('sessionid' in l.lower() for l in lines)
    return jsonify({'ok': True, 'lines': len(lines), 'sessionid_warning': not has_sessionid})

@app.route('/api/settings/cookies/preview')
def api_cookies_preview():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    account_id = request.args.get('account_id', 'p1')
    abs_cookie_path = os.path.join(BASE_DIR, 'downloads', 'cookies', f'{account_id}.txt')
    if not os.path.isfile(abs_cookie_path): return jsonify({'content': '(file not found)'})
    with open(abs_cookie_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read(4096)  # first 4KB preview
    return jsonify({'content': content})

@app.route('/api/settings/cookies/delete', methods=['POST'])
def api_cookies_delete():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json() or {}
    account_id = data.get('account_id', 'p1')
    abs_cookie_path = os.path.join(BASE_DIR, 'downloads', 'cookies', f'{account_id}.txt')
    if os.path.isfile(abs_cookie_path): os.remove(abs_cookie_path)
    db = get_db()
    db.execute("UPDATE ig_accounts SET cookie_path=NULL WHERE id=?", (account_id,))
    db.commit()
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
        with app.app_context():
            db = get_db()
            db.execute(
                'INSERT INTO ig_extractions (id,username,list_type,count,data_path,extracted_at) VALUES (?,?,?,?,?,?)',
                (extract_id, username, list_type, len(users), data_path, datetime.now(_dt_module.UTC).isoformat())
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
#  YouTube Account Rotation Manager
# ══════════════════════════════════════════════════════════════

def _get_available_yt_account():
    """
    Pick the best available YouTube account based on:
    1. Active status
    2. Usage count < 20 (autoresets daily)
    3. Cooldown (2 hours since first_error_at)
    """
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    now = datetime.now()
    
    # 1. Auto-Reset Logic: Reset counts for anything not used in 24 hours
    # This is a 'lazy' reset performed whenever a picker is called.
    db.execute('''
        UPDATE youtube_accounts 
        SET usage_count = 0 
        WHERE last_used_at < ?
    ''', ((now - timedelta(days=1)).isoformat(),))
    db.commit()

    # 2. Selection Logic
    # We want accounts that:
    # a. Are active
    # b. Have a cookie path
    # c. Are NOT in cooldown (either first_error_at IS NULL OR it was > 2 hours ago)
    # d. Have usage_count < 20
    # Ordered by last_used_at ascending to balance the load.
    
    two_hours_ago = (now - timedelta(hours=2)).isoformat()
    
    query = '''
        SELECT * FROM youtube_accounts
        WHERE is_active = 1
        AND cookie_path IS NOT NULL
        AND (first_error_at IS NULL OR first_error_at < ?)
        AND usage_count < max_usage
        ORDER BY last_used_at ASC
        LIMIT 1
    '''
    row = db.execute(query, (two_hours_ago,)).fetchone()
    db.close()
    if not row:
        return None
    acc = dict(row)
    # Guard: cookie file must actually exist on disk
    if not acc.get('cookie_path') or not os.path.isfile(acc['cookie_path']):
        # Mark as expired so it's skipped next time
        db2 = sqlite3.connect(DB_PATH)
        db2.execute("UPDATE youtube_accounts SET status='expired', cookie_path=NULL WHERE id=?", (acc['id'],))
        db2.commit()
        db2.close()
        return None
    return acc

def _update_yt_account_usage(acc_id, success=True, error_msg=""):
    """Update usage count or flag rate limits."""
    db = sqlite3.connect(DB_PATH)
    now = datetime.now().isoformat()
    if success:
        db.execute('''
            UPDATE youtube_accounts 
            SET usage_count = usage_count + 1, 
                last_used_at = ?, 
                first_error_at = NULL,
                status = 'ok'
            WHERE id = ?
        ''', (now, acc_id))
    else:
        status = 'ok'
        if 'rate-limit' in error_msg.lower() or 'unavailable' in error_msg.lower() or 'try again later' in error_msg.lower():
            status = 'rate_limited'
        elif 'cookie' in error_msg.lower() and 'expired' in error_msg.lower():
            status = 'expired'
            
        db.execute('''
            UPDATE youtube_accounts 
            SET first_error_at = COALESCE(first_error_at, ?),
                status = ?,
                last_used_at = ?
            WHERE id = ?
        ''', (now, status, now, acc_id))
    db.commit()
    db.close()

@app.route('/api/youtube/accounts')
def api_youtube_accounts():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    db = get_db()
    rows = db.execute('SELECT * FROM youtube_accounts ORDER BY id ASC').fetchall()
    res = []
    for r in rows:
        d = dict(r)
        d['has_cookie'] = bool(d['cookie_path'] and os.path.isfile(d['cookie_path']))
        # Calculate cooldown status
        d['in_cooldown'] = False
        if d['first_error_at']:
            err_at = datetime.fromisoformat(d['first_error_at'])
            if datetime.now() - err_at < timedelta(hours=2):
                d['in_cooldown'] = True
        res.append(d)
    return jsonify(res)

@app.route('/api/youtube/accounts/save', methods=['POST'])
def api_youtube_accounts_save():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json()
    acc_id = data.get('id')
    label = data.get('label')
    content = data.get('content', '').strip()
    
    if not acc_id or not label: return jsonify({'error': 'Missing ID or Label'}), 400
    
    cookie_path = None
    if content:
        yt_cookie_dir = os.path.join(BASE_DIR, 'cookies', 'youtube')
        os.makedirs(yt_cookie_dir, exist_ok=True)
        cookie_path = os.path.join(yt_cookie_dir, f'yt_acc_{acc_id}.txt')
        with open(cookie_path, 'w', encoding='utf-8') as f:
            f.write(content)
            
    db = get_db()
    if cookie_path:
        db.execute('UPDATE youtube_accounts SET label=?, cookie_path=?, status="ok", first_error_at=NULL, usage_count=0 WHERE id=?',
                   (label, cookie_path, acc_id))
    else:
        db.execute('UPDATE youtube_accounts SET label=? WHERE id=?', (label, acc_id))
    db.commit()
    return jsonify({'ok': True})

@app.route('/api/youtube/accounts/toggle', methods=['POST'])
def api_youtube_accounts_toggle():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json()
    acc_id = data.get('id')
    active = data.get('active', 1)
    db = get_db()
    db.execute('UPDATE youtube_accounts SET is_active=? WHERE id=?', (active, acc_id))
    db.commit()
    return jsonify({'ok': True})

@app.route('/api/youtube/accounts/delete-cookies', methods=['POST'])
def api_youtube_accounts_delete_cookies():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json()
    acc_id = data.get('id')
    db = get_db()
    row = db.execute('SELECT cookie_path FROM youtube_accounts WHERE id=?', (acc_id,)).fetchone()
    if row and row['cookie_path'] and os.path.isfile(row['cookie_path']):
        os.remove(row['cookie_path'])
    # Mark as expired — do NOT reset to 'ok' since cookies are gone
    db.execute("UPDATE youtube_accounts SET cookie_path=NULL, status='expired', first_error_at=NULL WHERE id=?", (acc_id,))
    db.commit()
    return jsonify({'ok': True})


# ══════════════════════════════════════════════════════════════
#  API — Split
# ══════════════════════════════════════════════════════════════
@app.route('/api/split/equal', methods=['POST'])
def api_split_equal():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    video         = request.files.get('video')
    n             = int(request.form.get('n', 30))
    use_cuda      = request.form.get('use_cuda', '0') == '1'
    output_format = request.form.get('output_format', 'original')  # 'original' | 'instagram'
    delete_source = request.form.get('delete_source', '0') == '1'

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
            output_format=output_format,
            progress_cb=lambda line, pct=None: _emit(job, line, pct),
        )
        if delete_source:
            deleted, msg = _safe_remove_source(tmp_path, files)
            _emit(job, ('🧹 ' if deleted else '⚠ ') + msg)
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
    output_format = (request.form.get('output_format', 'original') or 'original').strip().lower()
    delete_source = request.form.get('delete_source', '0') == '1'

    if not video:  return jsonify({'error': 'No video'}), 400
    if not clips:  return jsonify({'error': 'No clips defined'}), 400
    if output_format not in ('original', 'instagram'):
        return jsonify({'error': 'Invalid output_format. Use original or instagram'}), 400

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
            output_format=output_format,
            progress_cb=lambda line, pct=None: _emit(job, line, pct),
        )
        if delete_source:
            deleted, msg = _safe_remove_source(tmp_path, [f.get('path') for f in files])
            _emit(job, ('🧹 ' if deleted else '⚠ ') + msg)
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
    audio_only  = data.get('audio_only', False)
    dl_subs     = bool(data.get('download_subs', False))
    dl_thumb    = bool(data.get('download_thumb', False))
    concurrency = int(data.get('concurrency', 1))
    browser     = data.get('browser')
    cookie_mode = data.get('cookie_mode', 'file')  # default: always use uploaded cookie
    request_delay = float(data.get('request_delay', 30.0))
    
    # ── Multi-Account Rotation ──────────────────────────
    use_rotation = data.get('use_rotation', False)
    selected_account = None
    if use_rotation:
        selected_account = _get_available_yt_account()
        if not selected_account:
            return jsonify({'error': 'All YouTube accounts are exhausted or in cooldown. Please wait or add more accounts.'}), 429
        # Update YT_COOKIES_FILE just for this job's context? 
        # Actually, download_youtube takes a cookie_file path.
        cookie_file = selected_account['cookie_path']
        cookie_mode = 'file'
    else:
        # Fallback to legacy single-file logic
        cookie_file = YT_COOKIES_FILE if os.path.isfile(YT_COOKIES_FILE) else None  # always use if exists
    # ───────────────────────────────────────────────────

    if not urls: return jsonify({'error': 'No URLs'}), 400

    job_id = str(uuid.uuid4())
    job    = _make_job(job_id)

    def check_exists(vid_id, title=None):
        # Layer 1: Database Check (Fast)
        db = sqlite3.connect(DB_PATH)
        # Check by ID
        row = db.execute('SELECT file_path FROM reels WHERE id=? LIMIT 1', (vid_id,)).fetchone()
        if not row and title:
            # Check by Exact Title
            row = db.execute('SELECT file_path FROM reels WHERE title=? LIMIT 1', (title,)).fetchone()
        db.close()

        if not row:
            # ── DB has no record → treat as new, never skip ──
            # Clearing the DB fully resets duplicate detection.
            return False

        # DB has a record → confirm the physical file still exists
        fpath = row[0]
        if fpath and os.path.isfile(fpath):
            return True

        # DB has a record but file is missing → also confirm via ID on disk
        import glob as _g
        target_dir = data.get('output_dir', '').strip() or _read_setting('dir_yt') or os.path.join(DOWNLOADS_DIR, '_youtube')
        pattern_id = os.path.join(target_dir, '**', f'*[{vid_id}]*')
        if _g.glob(pattern_id, recursive=True):
            return True

        # Record existed in DB but file is gone → re-download
        return False

    def run():
        yt_dir = data.get('output_dir', '').strip()
        custom_dir = True
        if not yt_dir:
            yt_dir = _read_setting('dir_yt') or os.path.join(DOWNLOADS_DIR, '_youtube')
            custom_dir = False
        
        try:
            # Use rotation-provided cookie file if active
            c_file = cookie_file if use_rotation else (YT_COOKIES_FILE if cookie_mode == 'file' else None)
            
            def cb(line, pct=None):
                _emit(job, line, pct)
                if 'Error:' in line or 'ERROR:' in line:
                    if selected_account:
                        _update_yt_account_usage(selected_account['id'], success=False, error_msg=line)

            results = download_youtube(
                urls=urls, quality=quality, output_dir=yt_dir, audio_only=audio_only, custom_dir=custom_dir,
                download_subs=dl_subs, download_thumb=dl_thumb,
                concurrency=concurrency, browser=browser if cookie_mode == 'browser' else None, 
                cookie_file=c_file,
                request_delay=request_delay,
                check_exists_cb=check_exists,
                progress_cb=cb
            )

            # Update rotation usage logic
            if selected_account:
                # Count ok + skipped as successful usages
                ok_count = len([r for r in results if r.get('status') in ('ok', 'skipped')])
                if ok_count > 0:
                    # Update count per video as requested
                    for _ in range(ok_count):
                        _update_yt_account_usage(selected_account['id'], success=True)

            # Sync successes to Master DB
            try:
                db = sqlite3.connect(DB_PATH)
                for r in results:
                    if r.get('status') == 'ok':
                        db.execute('''
                            INSERT OR REPLACE INTO reels (id, url, title, file_path, status)
                            VALUES (?, ?, ?, ?, 'ok')
                        ''', (r['id'], r['url'], r.get('title', ''), r.get('file_path', '')))
                db.commit()
                db.close()
            except Exception as dbe:
                print(f"DB Sync Error: {dbe}")

            ok = sum(1 for r in results if r.get('status') in ('ok', 'skipped'))
            _finish(job, f'Processed {ok}/{len(results)} videos', results=results)
        except Exception as e:
            _finish(job, f'Fatal Error: {str(e)}', status='error')

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'job_id': job_id})

@app.route('/api/youtube/progress/<job_id>')
def api_youtube_progress(job_id):
    return _sse_stream(job_id)

@app.route('/api/youtube/upload-cookies', methods=['POST'])
def api_youtube_upload_cookies():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    if 'file' not in request.files: return jsonify({'error': 'No file'}), 400
    file = request.files['file']
    if not file.filename: return jsonify({'error': 'Empty filename'}), 400
    file.save(YT_COOKIES_FILE)
    return jsonify({'message': 'Cookies uploaded successfully', 'path': YT_COOKIES_FILE, 'exists': True})

@app.route('/api/youtube/cookies/status')
def api_youtube_cookies_status():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    return jsonify({'exists': os.path.isfile(YT_COOKIES_FILE)})

@app.route('/api/youtube/cookies/clear', methods=['POST'])
def api_youtube_cookies_clear():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    if os.path.isfile(YT_COOKIES_FILE):
        os.remove(YT_COOKIES_FILE)
    return jsonify({'ok': True, 'exists': False})

@app.route('/api/youtube/clear-logs', methods=['POST'])
def api_youtube_clear_logs():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    try:
        db = sqlite3.connect(DB_PATH)
        db.execute('DELETE FROM reels')
        db.commit()
        db.close()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500




@app.route('/api/youtube/reel-convert', methods=['POST'])
def api_youtube_reel_convert():
    """
    All-in-one: Download YouTube URL → convert to Instagram 9:16 Reel parts.
    Supports: equal split, optional clip range, title/part label/watermark overlays.
    """
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json() or {}

    url          = data.get('url', '').strip()
    output_dir   = data.get('output_dir', '').strip()
    title        = data.get('title', '').strip()
    watermark    = data.get('watermark', '').strip()
    part_secs    = int(data.get('part_duration', 60))
    show_title   = bool(data.get('show_title', True))
    show_part    = bool(data.get('show_part_label', True))
    show_wm      = bool(data.get('show_watermark', bool(watermark)))
    clip_start   = float(data.get('clip_start', 0))
    clip_end     = float(data.get('clip_end', 0))
    quality      = data.get('quality', '1080')
    title_pos_pct = float(data.get('title_pos_pct', 20.0))
    part_pos_pct  = float(data.get('part_pos_pct', 82.0))
    output_size   = data.get('output_size', 'instagram')
    clips         = data.get('clips', [])  # 'instagram' | 'original'
    overlay_image_path = (data.get('overlay_image_path') or '').strip()
    overlay_image_zoom = float(data.get('overlay_image_zoom', 1.0) or 1.0)
    overlay_image_comp_pct = max(1.0, min(100.0, float(data.get('overlay_image_comp_pct', 100.0) or 100.0)))
    overlay_mode   = data.get('overlay_mode', 'none')  # 'none' | 'manual' | 'auto'
    delete_source = bool(data.get('delete_source', False))
    browser       = data.get('browser')
    cookie_mode   = data.get('cookie_mode', 'file')  # default: always use uploaded cookie
    request_delay = float(data.get('request_delay', 30.0))
    use_rotation  = bool(data.get('use_rotation', False))

    # ── Multi-Account Rotation ──────────────────────────
    selected_account = None
    cookie_file = None
    if use_rotation:
        selected_account = _get_available_yt_account()
        if not selected_account:
            return jsonify({'error': 'All YouTube accounts are exhausted or in cooldown. Please wait or add more accounts.'}), 429
        cookie_file = selected_account['cookie_path']
        cookie_mode = 'file'
    else:
        cookie_file = YT_COOKIES_FILE if os.path.isfile(YT_COOKIES_FILE) else None  # always use if exists
    # ───────────────────────────────────────────────────

    if not url:
        return jsonify({'error': 'No YouTube URL provided'}), 400

    # If it's a Reels request, and the URL has both v= and list=, strip the list= part
    # because we want to convert the SPECIFIC video, not the whole mix/playlist.
    if 'v=' in url and 'list=' in url:
        # Simple regex to keep up to the next param or end of string
        match = re.search(r'(v=[^&]+)', url)
        if match:
            # Reconstruct basic watch URL
            url = "https://www.youtube.com/watch?" + match.group(1)

    job_id = str(uuid.uuid4())
    job    = _make_job(job_id)

    def run():
        # ── Step 1: Download ──────────────────────────────
        yt_dir = output_dir or _read_setting('dir_yt') or os.path.join(DOWNLOADS_DIR, '_youtube')
        os.makedirs(yt_dir, exist_ok=True)
        _emit(job, f'📥 Downloading: {url}', 5)

        def dcb(line, pct=None):
            _emit(job, line, pct)
            if 'Error:' in line or 'ERROR:' in line:
                if selected_account:
                    _update_yt_account_usage(selected_account['id'], success=False, error_msg=line)

        results = download_youtube(
            urls=[url], quality=quality, output_dir=yt_dir, audio_only=False,
            custom_dir=bool(output_dir), browser=browser if cookie_mode == 'browser' else None, 
            cookie_file=cookie_file,
            request_delay=request_delay,
            progress_cb=dcb
        )
        
        if results and results[0].get('status') == 'ok':
            if selected_account:
                _update_yt_account_usage(selected_account['id'], success=True)
        if not results or results[0].get('status') != 'ok':
            err = results[0].get('error', 'Download failed') if results else 'Download failed'
            _finish(job, f'Download failed: {err}', results=[])
            return
        
        # Save to Master DB
        try:
            r = results[0]
            db = sqlite3.connect(DB_PATH)
            db.execute('''
                INSERT OR REPLACE INTO reels (id, url, title, file_path, status)
                VALUES (?, ?, ?, ?, 'ok')
            ''', (r['id'], r['url'], r.get('title', ''), r.get('file_path', '')))
            db.commit()
            db.close()
        except Exception as dbe:
            print(f"Reel DB Save Error: {dbe}")

        video_path = results[0].get('file_path') or results[0].get('path', '')
        if not video_path or not os.path.isfile(video_path):
            # Fallback: find newest mp4 in yt_dir
            import glob as _g
            files = sorted(_g.glob(os.path.join(yt_dir, '**', '*.mp4'), recursive=True),
                           key=os.path.getmtime, reverse=True)
            video_path = files[0] if files else ''

        if not video_path or not os.path.isfile(video_path):
            _finish(job, 'Could not locate downloaded file', results=[])
            return

        video_title = results[0].get('title', title or 'Video')
        used_title  = title or video_title

        _emit(job, f'✅ Downloaded: {os.path.basename(video_path)}', 30)

        # ── Step 1b: Auto Thumbnail Overlay ──────────────────────────
        final_overlay_path = ''
        final_overlay_zoom = 1.0
        final_overlay_comp = 100.0

        if overlay_mode == 'manual' and overlay_image_path and os.path.isfile(overlay_image_path):
            final_overlay_path = overlay_image_path
            final_overlay_zoom = overlay_image_zoom
            final_overlay_comp = overlay_image_comp_pct
        elif overlay_mode == 'auto':
            # Download the YT thumbnail and use it automatically
            try:
                import glob as _gt
                thumb_tmpl = os.path.join(TEMP_DIR, f'yt_auto_thumb_{uuid.uuid4().hex[:8]}.%(ext)s')
                ytdlp_bin = _get_ytdlp_binary()
                thumb_cmd = ytdlp_bin + [
                    '--write-thumbnail', '--skip-download',
                    '--convert-thumbnails', 'jpg',
                    '--output', thumb_tmpl
                ]
                if cookie_file and os.path.isfile(cookie_file):
                    thumb_cmd += ['--cookies', cookie_file]
                thumb_cmd.append(url)
                subprocess.run(thumb_cmd, capture_output=True, timeout=30)
                # Find the downloaded thumbnail
                pattern = thumb_tmpl.replace('%(ext)s', '*')
                found = sorted(_gt.glob(pattern), key=os.path.getmtime, reverse=True)
                if found:
                    final_overlay_path = found[0]
                    final_overlay_zoom = 1.0   # System-managed
                    final_overlay_comp = 100.0  # System-managed
                    _emit(job, f'🖼 Auto thumbnail: {os.path.basename(final_overlay_path)}')
                else:
                    _emit(job, '⚠ Could not download auto thumbnail — skipping overlay')
            except Exception as te:
                _emit(job, f'⚠ Auto thumbnail error: {te}')

        # ── Step 2: Convert → Reel parts ─────────────────
        reel_dir = output_dir or os.path.join(os.path.dirname(video_path), '_reels')
        os.makedirs(reel_dir, exist_ok=True)
        _emit(job, f'🎬 Converting to 9:16 Reels (parts of {part_secs}s)…', 35)

        from modules.reel_converter import convert_to_reels
        conv = convert_to_reels(
            input_path      = video_path,
            output_dir      = reel_dir,
            title           = used_title,
            watermark       = watermark,
            part_duration_sec = part_secs,
            show_title      = show_title,
            show_part_label = show_part,
            show_watermark  = show_wm,
            title_pos_pct   = title_pos_pct,
            part_pos_pct    = part_pos_pct,
            output_size     = output_size,
            overlay_image_path = final_overlay_path,
            overlay_image_zoom = final_overlay_zoom,
            overlay_image_comp_pct = final_overlay_comp,
            clip_start_sec  = clip_start,
            clip_end_sec    = clip_end,
            clips           = clips,
            use_cuda        = CUDA_INFO.get('available', False),
            progress_cb     = lambda line: _emit(job, line),
        )

        parts  = conv.get('parts', [])
        errors = conv.get('errors', [])

        reel_results = [{'file': os.path.basename(p), 'path': p, 'status': 'ok'} for p in parts]
        reel_results += [{'file': e, 'status': 'error'} for e in errors]
        if delete_source and parts:
            deleted, msg = _safe_remove_source(video_path, parts)
            _emit(job, ('🧹 ' if deleted else '⚠ ') + msg)

        _emit(job, f'🎉 Done! {len(parts)} reel parts created.', 100)
        _finish(job, f'{len(parts)} parts created in {reel_dir}',
                results=reel_results, reel_dir=reel_dir, source_video=video_path)

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'job_id': job_id})



@app.route('/api/youtube/bulk-reel-convert', methods=['POST'])
def api_youtube_bulk_reel_convert():
    """
    Bulk: Download YouTube URLs sequentially, auto-inject thumbnail as bottom overlay,
    and split each video into 9:16 Instagram Reel parts.
    """
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json() or {}

    urls = [u.strip() for u in data.get('urls', []) if str(u).strip().startswith('http')]
    if not urls:
        return jsonify({'error': 'No valid YouTube URLs provided'}), 400

    output_dir    = data.get('output_dir', '').strip()
    watermark     = data.get('watermark', '').strip()
    part_secs     = int(data.get('part_duration', 60))
    quality       = data.get('quality', '1080')
    show_title    = bool(data.get('show_title', True))
    show_part     = bool(data.get('show_part_label', True))
    show_wm       = bool(data.get('show_watermark', bool(watermark)))
    title_pos_pct = float(data.get('title_pos_pct', 20.0))
    part_pos_pct  = float(data.get('part_pos_pct', 82.0))
    overlay_zoom  = float(data.get('overlay_image_zoom', 1.0) or 1.0)
    overlay_comp  = max(1.0, min(100.0, float(data.get('overlay_image_comp_pct', 100.0) or 100.0)))
    delete_source = bool(data.get('delete_source', False))
    use_rotation  = bool(data.get('use_rotation', False))
    # Always use uploaded cookie file if present
    global_cookie = YT_COOKIES_FILE if os.path.isfile(YT_COOKIES_FILE) else None

    job_id = str(uuid.uuid4())
    job    = _make_job(job_id)

    def run():
        from modules.reel_converter import convert_to_reels
        yt_dir = output_dir or _read_setting('dir_yt') or os.path.join(DOWNLOADS_DIR, '_youtube')
        os.makedirs(yt_dir, exist_ok=True)
        total = len(urls)
        _emit(job, f'Bulk Reel Generation started -- {total} URL(s) queued', 2)

        all_results = []

        for idx, url in enumerate(urls, 1):
            base_pct = int((idx - 1) / total * 95)
            _emit(job, f'[{idx}/{total}] Processing: {url}', base_pct)

            # Strip playlist params -- process single video
            if 'v=' in url and 'list=' in url:
                m = re.search(r'(v=[^&]+)', url)
                if m: url = 'https://www.youtube.com/watch?' + m.group(1)

            selected_account = None
            c_file = global_cookie
            if use_rotation:
                selected_account = _get_available_yt_account()
                if not selected_account:
                    _emit(job, 'Skipping: No accounts available (all exhausted/cooldown)', None)
                    all_results.append({'url': url, 'status': 'error', 'error': 'No accounts available'})
                    continue
                c_file = selected_account['cookie_path']

            def dcb(line, pct=None):
                _emit(job, '  ' + line, None)

            # Download video + thumbnail automatically
            dl_results = download_youtube(
                urls=[url], quality=quality, output_dir=yt_dir,
                audio_only=False, custom_dir=bool(output_dir),
                browser=None, cookie_file=c_file,
                download_thumb=True,
                request_delay=30.0,
                progress_cb=dcb
            )

            if not dl_results or dl_results[0].get('status') not in ('ok', 'skipped'):
                err = dl_results[0].get('error', 'Download failed') if dl_results else 'Download failed'
                _emit(job, f'Download failed: {err}', None)
                if selected_account:
                    _update_yt_account_usage(selected_account['id'], success=False, error_msg=err)
                all_results.append({'url': url, 'status': 'error', 'error': err})
                continue

            if selected_account:
                _update_yt_account_usage(selected_account['id'], success=True)

            r = dl_results[0]
            video_path  = r.get('file_path', '')
            thumb_path  = r.get('thumbnail', '')
            video_title = r.get('title', 'Video')

            # Fallback: find newest mp4 in yt_dir
            if not video_path or not os.path.isfile(video_path):
                import glob as _g
                files = sorted(_g.glob(os.path.join(yt_dir, '**', '*.mp4'), recursive=True),
                               key=os.path.getmtime, reverse=True)
                video_path = files[0] if files else ''

            if not video_path or not os.path.isfile(video_path):
                _emit(job, 'Could not locate downloaded MP4', None)
                all_results.append({'url': url, 'status': 'error', 'error': 'MP4 not found after download'})
                continue

            _emit(job, f'Downloaded: {os.path.basename(video_path)} | Thumbnail: {os.path.basename(thumb_path) if thumb_path else "none"}', None)

            # Convert to reel parts, injecting auto-downloaded thumbnail
            reel_dir = output_dir or os.path.join(os.path.dirname(video_path), '_reels')
            os.makedirs(reel_dir, exist_ok=True)
            _emit(job, f'Splitting into {part_secs}s reel parts...', None)

            conv = convert_to_reels(
                input_path         = video_path,
                output_dir         = reel_dir,
                title              = video_title,
                watermark          = watermark,
                part_duration_sec  = part_secs,
                show_title         = show_title,
                show_part_label    = show_part,
                show_watermark     = show_wm,
                title_pos_pct      = title_pos_pct,
                part_pos_pct       = part_pos_pct,
                output_size        = 'instagram',
                overlay_image_path = thumb_path,
                overlay_image_zoom = overlay_zoom,
                overlay_image_comp_pct = overlay_comp,
                clip_start_sec     = 0,
                clip_end_sec       = 0,
                use_cuda           = CUDA_INFO.get('available', False),
                progress_cb        = lambda line: _emit(job, '  ' + line),
            )

            parts  = conv.get('parts', [])
            cvt_errors = conv.get('errors', [])
            _emit(job, f'{len(parts)} reel parts created for: {video_title}', None)

            for p in parts:
                all_results.append({'file': os.path.basename(p), 'path': p, 'status': 'ok'})
            for e in cvt_errors:
                all_results.append({'file': e, 'status': 'error'})

            if delete_source and parts:
                deleted, msg = _safe_remove_source(video_path, parts)
                _emit(job, msg)

        ok_count = len([r for r in all_results if r.get('status') == 'ok'])
        _emit(job, f'Bulk complete! {ok_count} reel parts from {total} video(s).', 100)
        _finish(job, f'Bulk done: {ok_count} parts', results=all_results)

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'job_id': job_id})


@app.route('/api/youtube/reel-convert-local', methods=['POST'])
def api_youtube_reel_convert_local():
    """Convert a local video file (already downloaded) to Reel parts."""
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json() or {}

    video_path = data.get('video_path', '').strip()
    if not video_path or not os.path.isfile(video_path):
        return jsonify({'error': 'File not found: ' + video_path}), 400

    title        = data.get('title', '').strip()
    watermark    = data.get('watermark', '').strip()
    output_dir   = data.get('output_dir', '').strip()
    part_secs    = int(data.get('part_duration', 60))
    show_title   = bool(data.get('show_title', True))
    show_part    = bool(data.get('show_part_label', True))
    show_wm      = bool(data.get('show_watermark', bool(watermark)))
    clip_start   = float(data.get('clip_start', 0))
    clip_end     = float(data.get('clip_end', 0))
    title_pos_pct = float(data.get('title_pos_pct', 20.0))
    part_pos_pct  = float(data.get('part_pos_pct', 82.0))
    output_size   = data.get('output_size', 'instagram')
    clips         = data.get('clips', [])  # 'instagram' | 'original'
    overlay_image_path = (data.get('overlay_image_path') or '').strip()
    overlay_image_zoom = float(data.get('overlay_image_zoom', 1.0) or 1.0)
    overlay_image_comp_pct = max(1.0, min(100.0, float(data.get('overlay_image_comp_pct', 100.0) or 100.0)))
    delete_source = bool(data.get('delete_source', False))

    job_id = str(uuid.uuid4())
    job    = _make_job(job_id)

    def run():
        reel_dir = output_dir or os.path.join(os.path.dirname(video_path), '_reels')
        os.makedirs(reel_dir, exist_ok=True)
        _emit(job, f'🎬 Converting: {os.path.basename(video_path)}', 5)
        from modules.reel_converter import convert_to_reels
        conv = convert_to_reels(
            input_path=video_path, output_dir=reel_dir,
            title=title, watermark=watermark,
            part_duration_sec=part_secs,
            show_title=show_title, show_part_label=show_part, show_watermark=show_wm,
            title_pos_pct=title_pos_pct, part_pos_pct=part_pos_pct,
            output_size=output_size,
            overlay_image_path=overlay_image_path,
            overlay_image_zoom=overlay_image_zoom,
            overlay_image_comp_pct=overlay_image_comp_pct,
            clip_start_sec=clip_start, clip_end_sec=clip_end, clips=clips,
            progress_cb=lambda line: _emit(job, line),
        )
        parts  = conv.get('parts', [])
        errors = conv.get('errors', [])
        reel_results = [{'file': os.path.basename(p), 'path': p, 'status': 'ok'} for p in parts]
        reel_results += [{'file': e, 'status': 'error'} for e in errors]
        if delete_source and parts:
            deleted, msg = _safe_remove_source(video_path, parts)
            _emit(job, ('🧹 ' if deleted else '⚠ ') + msg)
        _finish(job, f'{len(parts)} parts in {reel_dir}',
                results=reel_results, reel_dir=reel_dir)

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'job_id': job_id})

@app.route('/api/youtube/overlay-image-upload', methods=['POST'])
def api_youtube_overlay_image_upload():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    image = request.files.get('image')
    if not image:
        return jsonify({'error': 'No image file provided'}), 400
    ext = os.path.splitext(image.filename or '')[1].lower()
    if ext not in ('.png', '.jpg', '.jpeg', '.webp', '.bmp'):
        return jsonify({'error': 'Unsupported image type'}), 400
    dest = os.path.join(UPLOAD_TEMP, f'overlay_{uuid.uuid4().hex}{ext or ".png"}')
    image.save(dest)
    return jsonify({'path': dest, 'name': os.path.basename(dest)})


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
            browser = data.get('browser')
            cookie_mode = data.get('cookie_mode', 'file')  # default: always use uploaded cookie
            use_rotation = bool(data.get('use_rotation', False))
            request_delay = float(data.get('request_delay', 30.0))
            selected_account = None
            if use_rotation:
                selected_account = _get_available_yt_account()
                if selected_account:
                    c_file = selected_account['cookie_path']
                    cookie_mode = 'file'
                else:
                    _emit(job, '⚠ All accounts in cooldown — using default cookie.')
                    c_file = YT_COOKIES_FILE if cookie_mode == 'file' else None
            else:
                c_file = YT_COOKIES_FILE if cookie_mode == 'file' else None
            out = output_dir or _read_setting('dir_yt') or os.path.join(DOWNLOADS_DIR, '_youtube_mp3')
            os.makedirs(out, exist_ok=True)
            def yta_cb(l, p=None):
                _emit(job, l, p)
                if selected_account and ('Error:' in l or 'ERROR:' in l):
                    _update_yt_account_usage(selected_account['id'], success=False, error_msg=l)
            results = download_youtube(
                urls=yt_urls, quality='best', output_dir=out, audio_only=True,
                browser=browser if cookie_mode == 'browser' else None,
                cookie_file=c_file, request_delay=request_delay,
                progress_cb=yta_cb
            )
            if selected_account and any(r.get('status') == 'ok' for r in results):
                _update_yt_account_usage(selected_account['id'], success=True)
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
    from modules.account_manager import get_active_profile, get_account_status
    profile = get_active_profile()
    if not profile:
        return {'in_cooldown': True, 'used': 10, 'remaining': 0, 'window_start': 0, 'cooldown_ends': 0, 'seconds_left': 7200, 'error': 'No active/healthy profiles available'}
    return get_account_status(profile['id'])

def _record_uid_dl(n, profile_id=None):
    from modules.account_manager import record_account_usage, get_active_profile
    pid = profile_id
    if not pid:
        p = get_active_profile()
        pid = p['id'] if p else None
    if pid:
        record_account_usage(pid, n)

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
    opt_watermark = data.get('opt_watermark', False)
    opt_split     = data.get('opt_split', False)
    opt_parts     = data.get('opt_parts', False)
    delete_source = bool(data.get('delete_source', False))
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
            fmt = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best'
        else:
            fmt = f'bestvideo[height<={quality}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<={quality}]+bestaudio/best'

        cmd = [
            sys.executable, '-m', 'yt_dlp', '--rm-cache-dir',
            '--format', fmt,
            '--merge-output-format', 'mp4',
            '--output', os.path.join(out_dir, '%(id)s.%(ext)s'),
            '--write-info-json', '--no-warnings',
            '--ignore-errors',  # skip unavailable videos
        ]
        if os.path.isfile(COOKIES_FILE):
            cmd += ['--cookies', COOKIES_FILE]
            
        _emit(job, f'Looking up latest reels for @{username}...')
        from modules.instagram_social import lookup_user
        info = lookup_user(username)  # we don't pass COOKIES_FILE, let it auto-rotate
        
        if 'error' in info:
            _emit(job, f'API Error: {info["error"]}')
            _finish(job, 'Failed to fetch user profile metadata.', error=True)
            return
            
        shortcodes = info.get('recent_posts', [])
        used_profile = info.get('profile_id', None)
        if not shortcodes:
            _emit(job, 'No recent posts found for this user.')
            _finish(job, 'Extraction complete (0 found).', error=True)
            return

        # Duplicate checking
        with app.app_context():
            db = get_db()
            fresh_shortcodes = []
            for sc in shortcodes:
                # yt-dlp 'id' equals shortcode usually
                if not db.execute("SELECT 1 FROM reels WHERE id=?", (sc,)).fetchone():
                    fresh_shortcodes.append(sc)
                if len(fresh_shortcodes) >= limit:
                    break
                
        if not fresh_shortcodes:
            _emit(job, f'All {len(shortcodes)} recent reels are already downloaded (duplicates skipped).')
            _finish(job, 'Skipped 100% duplicates.')
            return

        # Cap at requested limit again just in case
        shortcodes = fresh_shortcodes[:limit]
        urls = [f'https://www.instagram.com/p/{sc}/' for sc in shortcodes]
        
        # Determine actual cookie to give to yt-dlp
        working_cookie_path = None
        from modules.account_manager import get_active_profile
        p = get_active_profile()
        if p and p['cookie_path']:
             working_cookie_path = p['cookie_path']
        elif os.path.isfile(COOKIES_FILE):
             working_cookie_path = COOKIES_FILE
             
        if working_cookie_path:
             try:
                 idx = cmd.index('--cookies')
                 cmd[idx+1] = working_cookie_path
             except ValueError:
                 cmd += ['--cookies', working_cookie_path]
                 
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

        # Build file-aware results for downstream pipeline
        file_results = []
        for fname in os.listdir(out_dir):
            if fname.endswith('.mp4'):
                fp = os.path.join(out_dir, fname)
                file_results.append({
                    'status': 'ok',
                    'id': os.path.splitext(fname)[0],
                    'path': fp,
                    'file_path': fp
                })
        if file_results:
            results = file_results

        final = _get_uid_dl_status()

        # ── Post-download pipeline ────────────────────────────
        if any([opt_watermark, opt_split, opt_parts]):
            from modules.pipeline import run_pipeline
            settings = _get_settings_dict()
            pipeline_results = []
            for r in results:
                fp = r.get('file_path') or r.get('path', '')
                if fp and os.path.isfile(fp):
                    _emit(job, f'▶ Post-processing: {os.path.basename(fp)}')
                    final_files = run_pipeline(
                        file_path=fp,
                        settings=settings,
                        opt_watermark=opt_watermark,
                        opt_split=opt_split,
                        opt_parts=opt_parts,
                        progress_cb=lambda line, pct=None: _emit(job, line, pct)
                    )
                    for ff in final_files:
                        pipeline_results.append({'status': 'ok', 'path': ff, 'file_path': ff})
                    if delete_source:
                        deleted, msg = _safe_remove_source(fp, final_files)
                        _emit(job, ('🧹 ' if deleted else '⚠ ') + msg)
            if pipeline_results:
                results = pipeline_results

        _finish(job, f'Downloaded {downloaded} reels from @{username}',
                downloaded=downloaded, results=results,
                in_cooldown=final['in_cooldown'],
                seconds_left=final['seconds_left'],
                remaining=final['remaining'])

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'job_id': job_id})
# ══════════════════════════════════════════════════════════════
#  API — Multi-Account Auto Poster
# ══════════════════════════════════════════════════════════════

@app.route('/api/poster/accounts', methods=['GET'])
def api_poster_accounts():
    """Return all 5 poster account configs (passwords masked)."""
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    rows = get_db().execute('SELECT * FROM poster_accounts ORDER BY id').fetchall()
    accounts = []
    for r in rows:
        a = dict(r)
        if a.get('password'):
            a['password'] = '********'
        accounts.append(a)
    return jsonify({'accounts': accounts})


@app.route('/api/poster/accounts/<int:acc_id>/save', methods=['POST'])
def api_poster_account_save(acc_id):
    """Save config for a single poster account slot."""
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    if acc_id not in range(1, 6): return jsonify({'error': 'Invalid account slot'}), 400
    data = request.get_json() or {}
    db   = get_db()

    allowed = ['label', 'username', 'folder_path', 'caption', 'tags',
               'max_posts_batch', 'cool_minutes', 'interval_minutes', 'enabled', 'session_ttl_hours']
    sets  = []
    vals  = []
    for k in allowed:
        if k in data:
            v = data[k]
            if k == 'session_ttl_hours':
                try:
                    v = max(1, min(168, int(v)))
                except Exception:
                    v = 24
            sets.append(f'{k}=?')
            vals.append(v)

    # Only update password if user actually typed a new one
    raw_pass = data.get('password', '')
    if raw_pass and raw_pass != '********':
        sets.append('password=?')
        vals.append(raw_pass)

    if sets:
        vals.append(acc_id)
        db.execute(f'UPDATE poster_accounts SET {", ".join(sets)} WHERE id=?', vals)
        db.commit()

    return jsonify({'ok': True})


@app.route('/api/poster/accounts/status', methods=['GET'])
def api_poster_status():
    """Live status of all accounts: status, note, posts_in_window, last_posted_at."""
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    rows = get_db().execute(
        'SELECT id, label, username, enabled, status, note, posts_in_window, max_posts_batch, last_posted_at, window_start, cool_minutes FROM poster_accounts ORDER BY id'
    ).fetchall()
    accounts = []
    for r in rows:
        a = dict(r)
        # Compute cooling_remaining if in cooling
        if a.get('status') == 'cooling' and a.get('window_start'):
            try:
                from datetime import datetime, timedelta
                ws = datetime.fromisoformat(a['window_start'])
                now_utc = datetime.now(timezone.utc)
                if ws.tzinfo is None:
                    ws = ws.replace(tzinfo=timezone.utc)
                elapsed  = (now_utc - ws).total_seconds() / 60
                remaining = max(0, (a['cool_minutes'] or 120) - elapsed)
                a['cooling_remaining_mins'] = int(remaining)
            except Exception:
                a['cooling_remaining_mins'] = None
        else:
            a['cooling_remaining_mins'] = None
        accounts.append(a)
    return jsonify({'accounts': accounts})


@app.route('/api/poster/log', methods=['GET'])
def api_poster_log():
    """Last 50 poster log entries."""
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    rows = get_db().execute(
        'SELECT pl.*, pa.label, pa.username FROM poster_log pl LEFT JOIN poster_accounts pa ON pl.account_id=pa.id ORDER BY pl.id DESC LIMIT 50'
    ).fetchall()
    return jsonify({'log': [dict(r) for r in rows]})


@app.route('/api/poster/accounts/<int:acc_id>/reset', methods=['POST'])
def api_poster_reset(acc_id):
    """Reset cooling window for an account."""
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    db = get_db()
    db.execute('UPDATE poster_accounts SET posts_in_window=0, window_start=NULL, status="idle", note="Manual reset" WHERE id=?', (acc_id,))
    db.commit()
    return jsonify({'ok': True})


def _classify_connection_response(error_text: str) -> dict:
    txt = (error_text or '').lower()
    classification = 'error'
    is_ip_ban = 0
    is_rate_limited = 0

    if any(k in txt for k in ['bad password', 'incorrect password', 'login_required', 'invalid user']):
        classification = 'invalid_credentials'
    elif any(k in txt for k in ['challenge', 'checkpoint', 'two_factor']):
        classification = 'challenge'
    elif any(k in txt for k in ['429', 'too many requests', 'please wait a few minutes']):
        classification = 'ip_ban'
        is_ip_ban = 1
        is_rate_limited = 1
    elif any(k in txt for k in ['ip', 'temporarily blocked', 'sentry_block']):
        classification = 'ip_ban'
        is_ip_ban = 1
    return {
        'classification': classification,
        'is_ip_ban': is_ip_ban,
        'is_rate_limited': is_rate_limited,
    }


def _latest_connection_test(db, acc_id: int):
    return db.execute(
        '''SELECT id, account_id, outcome, status_code, summary, raw_response, is_ip_ban, is_rate_limited, tested_at
           FROM poster_connection_tests
           WHERE account_id=?
           ORDER BY id DESC LIMIT 1''',
        (acc_id,)
    ).fetchone()


@app.route('/api/poster/accounts/<int:acc_id>/connection-test', methods=['POST'])
def api_poster_connection_test(acc_id):
    """On-demand auth/connectivity test with throttle: max 2 attempts / 2 hours per account."""
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401
    if acc_id not in range(1, 6):
        return jsonify({'error': 'Invalid account slot'}), 400

    db = get_db()
    account = db.execute(
        'SELECT id, label, username, password, session_ttl_hours, session_established_at FROM poster_accounts WHERE id=?',
        (acc_id,)
    ).fetchone()
    if not account:
        return jsonify({'error': 'Account not found'}), 404

    username = (account['username'] or '').strip()
    password = (account['password'] or '').strip()
    if not username or not password:
        return jsonify({'error': 'Username/password not configured for this account'}), 400

    now_utc = datetime.now(timezone.utc)
    window_start = (now_utc - timedelta(hours=2)).isoformat()
    used = db.execute(
        '''SELECT COUNT(*) AS c FROM poster_connection_tests
           WHERE account_id=? AND tested_at >= ?''',
        (acc_id, window_start)
    ).fetchone()
    attempts_used = int((used['c'] if used else 0) or 0)
    remaining = max(0, 2 - attempts_used)
    if attempts_used >= 2:
        oldest = db.execute(
            '''SELECT tested_at FROM poster_connection_tests
               WHERE account_id=? AND tested_at >= ?
               ORDER BY tested_at ASC LIMIT 1''',
            (acc_id, window_start)
        ).fetchone()
        retry_at = None
        retry_mins = 120
        if oldest and oldest['tested_at']:
            try:
                retry_at_dt = datetime.fromisoformat(oldest['tested_at']) + timedelta(hours=2)
                retry_at = retry_at_dt.isoformat()
                if retry_at_dt.tzinfo is None:
                    retry_at_dt = retry_at_dt.replace(tzinfo=timezone.utc)
                retry_mins = max(1, int((retry_at_dt - now_utc).total_seconds() // 60))
            except Exception:
                pass
        last = _latest_connection_test(db, acc_id)
        return jsonify({
            'ok': False,
            'throttled': True,
            'error': f'Limit reached: 2 tests per 2 hours. Try again in ~{retry_mins} minute(s).',
            'limit': {'max_attempts': 2, 'window_minutes': 120, 'attempts_used': attempts_used, 'attempts_remaining': remaining},
            'retry_at': retry_at,
            'last_result': dict(last) if last else None,
        }), 429

    outcome = 'error'
    status_code = None
    summary = ''
    raw_response = ''
    is_ip_ban = 0
    is_rate_limited = 0
    ok = False
    try:
        from modules.poster import test_login_with_local_session
        payload = test_login_with_local_session(dict(account))
        outcome = 'success'
        summary = payload.get('summary') or f'Login test passed for @{username}. Connection looks good.'
        raw_response = payload.get('raw_response') or 'login_ok'
        ok = True
    except Exception as e:
        raw_response = str(e)[:2000]
        status_code = getattr(e, 'status_code', None)
        flags = _classify_connection_response(raw_response)
        outcome = flags['classification']
        is_ip_ban = flags['is_ip_ban']
        is_rate_limited = flags['is_rate_limited']
        summary = 'Connection test failed.'
        if outcome == 'invalid_credentials':
            summary = 'Invalid username or password.'
        elif outcome == 'challenge':
            summary = 'Instagram challenge/checkpoint required for this login.'
        elif outcome == 'ip_ban':
            summary = 'Possible IP block/rate-limit detected from Instagram response.'

    db.execute(
        '''INSERT INTO poster_connection_tests
           (account_id, outcome, status_code, summary, raw_response, is_ip_ban, is_rate_limited)
           VALUES (?,?,?,?,?,?,?)''',
        (acc_id, outcome, status_code, summary, raw_response, is_ip_ban, is_rate_limited)
    )
    db.commit()
    last = _latest_connection_test(db, acc_id)
    attempts_used += 1
    return jsonify({
        'ok': ok,
        'throttled': False,
        'account_id': acc_id,
        'result': dict(last) if last else None,
        'limit': {'max_attempts': 2, 'window_minutes': 120, 'attempts_used': attempts_used, 'attempts_remaining': max(0, 2 - attempts_used)},
    }), (200 if ok else 400)


@app.route('/api/poster/accounts/<int:acc_id>/connection-test', methods=['GET'])
def api_get_poster_connection_test(acc_id):
    """Get latest connection test console payload for one account."""
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401
    if acc_id not in range(1, 6):
        return jsonify({'error': 'Invalid account slot'}), 400

    db = get_db()
    last = _latest_connection_test(db, acc_id)
    window_start = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    used = db.execute(
        '''SELECT COUNT(*) AS c FROM poster_connection_tests
           WHERE account_id=? AND tested_at >= ?''',
        (acc_id, window_start)
    ).fetchone()
    attempts_used = int((used['c'] if used else 0) or 0)
    return jsonify({
        'ok': True,
        'result': dict(last) if last else None,
        'limit': {'max_attempts': 2, 'window_minutes': 120, 'attempts_used': attempts_used, 'attempts_remaining': max(0, 2 - attempts_used)},
    })


# Legacy routes kept for backward compat
@app.route('/api/settings/poster/save', methods=['POST'])
def api_save_poster_settings():
    return jsonify({'ok': True, 'note': 'Use /api/poster/accounts/<id>/save instead'})

@app.route('/api/settings/poster/auth', methods=['POST'])
def api_save_poster_auth():
    return jsonify({'ok': True, 'note': 'Use /api/poster/accounts/<id>/save instead'})


# ── Boot ──────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════
#  Bulk Local Reels
# ══════════════════════════════════════════════════════════════
@app.route('/process/bulk-local')
def bulk_local_page():
    if not session.get('logged_in'): return redirect(url_for('login'))
    return render_template('bulk_local.html')

@app.route('/api/local/bulk-convert', methods=['POST'])
def api_local_bulk_convert():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401

    master_folder = request.form.get('master_folder', '').strip()
    try:
        file_limit = int(request.form.get('file_limit', 10))
    except:
        file_limit = 10
    output_size = request.form.get('output_size', 'instagram')
    split_mode = request.form.get('split_mode', 'equal')
    try:
        part_duration = int(request.form.get('part_duration', 60))
    except:
        part_duration = 60

    try:
        clip_start = float(request.form.get('clip_start', 0))
    except:
        clip_start = 0.0
    try:
        clip_end = float(request.form.get('clip_end', 0))
    except:
        clip_end = 0.0

    clips_json = request.form.get('clips', '[]')
    try:
        import json
        clips = json.loads(clips_json)
    except:
        clips = []

    show_title = request.form.get('show_title') == 'true'
    show_part = request.form.get('show_part') == 'true'
    show_watermark = request.form.get('show_watermark') == 'true'

    # Handle Thumbnail Upload
    thumb_path = ''
    if 'thumbnail' in request.files:
        t_file = request.files['thumbnail']
        if t_file and t_file.filename:
            t_ext = os.path.splitext(t_file.filename)[1]
            thumb_path = os.path.join(TEMP_DIR, f"bulk_local_thumb_{uuid.uuid4().hex[:8]}{t_ext}")
            t_file.save(thumb_path)

    watermark_text = _read_setting('watermark_text', '') if show_watermark else ''

    def generate():
        import glob
        db = get_db()
        
        if not os.path.isdir(master_folder):
            yield f"data: [ERROR] Master folder not found: {master_folder}\n\n"
            yield "data: [DONE]\n\n"
            return
            
        yield f"data: 📁 Scanning folder: {master_folder}\n\n"
        
        # Output folder (processed-file) inside the master folder
        output_parent = os.path.join(master_folder, "processed-file")
        os.makedirs(output_parent, exist_ok=True)
        
        # Only scan non-recursive
        files = []
        for ext in ('*.mp4', '*.mov', '*.mkv'):
            files.extend(glob.glob(os.path.join(master_folder, ext)))
            
        yield f"data: Found {len(files)} video files.\n\n"
        
        processed_count = 0
        for fpath in files:
            if processed_count >= file_limit:
                yield f"data: 🛑 Reached file limit ({file_limit}). Stopping.\n\n"
                break
                
            fname = os.path.basename(fpath)
            
            # Check Deduplication
            if db.execute('SELECT 1 FROM local_bulk_history WHERE folder_path=? AND file_name=?', (master_folder, fname)).fetchone():
                yield f"data: ⏭️ Skipping duplicate: {fname}\n\n"
                continue
                
            yield f"data: \ndata: ⏳ Processing ({processed_count+1}): {fname}\n\n"
            
            # Auto-title uses filename without extension
            title_val = os.path.splitext(fname)[0] if show_title else ''
            
            # Call Converter
            from modules.reel_converter import convert_to_reels
            
            def update_status(line):
                # We yield each log line back to frontend
                pass # Generator can't be easily called from within nested callback directly without queue. We'll capture it via a mutable list.
                
            logs_queue = []
            def queue_log(line):
                logs_queue.append(line)
                
            # Actually, since convert_to_reels is synchronous, we cannot stream its internal logs perfectly during execution unless we run it in a thread.
            # To keep it simple and stable, we'll just run it and then yield a completion message per file.
            yield "data:   [Encoding started... please wait]\n\n"
            
            try:
                res = convert_to_reels(
                    input_path=fpath,
                    output_dir=output_parent,
                    title=title_val,
                    watermark=watermark_text,
                    part_duration_sec=part_duration,
                    show_title=show_title,
                    show_part_label=show_part,
                    show_watermark=show_watermark,
                    title_pos_pct=28.0,
                    part_pos_pct=82.0,
                    output_size=output_size,
                    overlay_image_path=thumb_path,
                    overlay_image_zoom=1.0,
                    overlay_image_comp_pct=100.0,
                    clip_start_sec=clip_start if split_mode == 'clip' else 0.0,
                    clip_end_sec=clip_end if split_mode == 'clip' else 0.0,
                    use_cuda=CUDA_INFO.get('available', False),
                    progress_cb=None
                )
                
                if res.get('parts'):
                    db.execute('INSERT INTO local_bulk_history (folder_path, file_name) VALUES (?, ?)', (master_folder, fname))
                    db.commit()
                    yield f"data: ✅ Successfully generated {len(res['parts'])} parts.\n\n"
                    processed_count += 1
                else:
                    errs = ', '.join(res.get('errors', []))
                    yield f"data: ❌ Failed: {errs}\n\n"
            except Exception as e:
                yield f"data: ❌ Exception: {str(e)}\n\n"

        yield f"data: \ndata: 🎉 Batch complete! Processed {processed_count} files.\n\n"
        yield "data: [DONE]\n\n"

    return Response(generate(), mimetype='text/event-stream')

if __name__ == '__main__':
    init_db()

    # Pre-populate P1-P5 in DB
    from modules.account_manager import ensure_default_profiles_exist
    with app.app_context():
        ensure_default_profiles_exist()

    # Boot Poster Daemon
    def get_context_for_poster():
        with app.app_context():
            db = get_db()
            settings = _get_settings_dict()
            return db, settings

    from modules.poster import run_poster_daemon
    threading.Thread(target=run_poster_daemon, daemon=True).start()

    from modules.post_scheduler import run_post_scheduler
    threading.Thread(target=run_post_scheduler, daemon=True).start()

    cuda_str = 'CUDA ACTIVE' if CUDA_INFO['available'] else 'CPU Mode'
    print(f'\n  *** Nikethan Reels Toolkit ***')
    print(f'  GPU: {cuda_str}')
    print(f'  Starting on http://localhost:5056\n')
    app.run(host='0.0.0.0', port=5056, debug=False)

