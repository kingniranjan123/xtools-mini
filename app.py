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
    for a in accounts:
        if a.get('password'):
            a['password'] = '********'
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
    db       = get_db()
    q = 'SELECT * FROM post_queue'
    clauses, vals = [], []
    if platform: clauses.append('platform=?'); vals.append(platform)
    if status:   clauses.append('status=?');   vals.append(status)
    if clauses:  q += ' WHERE ' + ' AND '.join(clauses)
    q += ' ORDER BY scheduled_at ASC, id DESC LIMIT 100'
    rows = db.execute(q, vals).fetchall()
    return jsonify({'items': [dict(r) for r in rows]})


@app.route('/api/post-queue/add', methods=['POST'])
def api_post_queue_add():
    """Add one or more items to the post queue."""
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data  = request.get_json() or {}
    items = data.get('items', [data])   # accept single or list
    db    = get_db()
    added = []
    for item in items:
        fp = item.get('file_path', '').strip()
        if not fp:
            continue
        cur = db.execute(
            '''INSERT INTO post_queue
               (platform, account_slot, file_path, title, description, tags,
                privacy, category_id, thumbnail_path, scheduled_at, ai_generated)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            (
                item.get('platform', 'instagram'),
                item.get('account_slot', 1),
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
    return jsonify({'ok': True, 'added': added, 'count': len(added)})


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
        home_ch = _read_setting('home_channel', '').strip().lstrip('@').lower()
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
        home_ch = _read_setting('home_channel', '').strip().lstrip('@').lower()
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
    for k in ['yt_api_key', 'home_channel', 'openrouter_api_key', 'content_niche']:
        if k in data:
            db.execute('INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (k, data[k]))
    db.commit()
    return jsonify({'ok': True})


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
    niche = cfg.get('content_niche', '')
    try:
        from modules.ai_generator import generate_youtube_content
        result = generate_youtube_content(topic, api_key, niche, is_short)
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
    niche = cfg.get('content_niche', '')
    try:
        from modules.ai_generator import generate_instagram_content
        result = generate_instagram_content(topic, api_key, niche, content_type)
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
    account_id = data.get('account_id', 'p1')
    content = data.get('content', '').strip()
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
    return jsonify({'ok': True, 'lines': len(lines)})

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

    if not url:
        return jsonify({'error': 'No YouTube URL provided'}), 400

    job_id = str(uuid.uuid4())
    job    = _make_job(job_id)

    def run():
        # ── Step 1: Download ──────────────────────────────
        yt_dir = output_dir or _read_setting('dir_yt') or os.path.join(DOWNLOADS_DIR, '_youtube')
        os.makedirs(yt_dir, exist_ok=True)
        _emit(job, f'📥 Downloading: {url}', 5)

        results = download_youtube(
            urls=[url], quality=quality, output_dir=yt_dir, audio_only=False,
            custom_dir=bool(output_dir),
            progress_cb=lambda line, pct=None: _emit(job, line, pct)
        )
        if not results or results[0].get('status') != 'ok':
            err = results[0].get('error', 'Download failed') if results else 'Download failed'
            _finish(job, f'Download failed: {err}', results=[])
            return

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

        # ── Step 2: Convert → Reel parts ─────────────────
        reel_dir = os.path.join(os.path.dirname(video_path), '_reels')
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
            clip_start_sec  = clip_start,
            clip_end_sec    = clip_end,
            progress_cb     = lambda line: _emit(job, line),
        )

        parts  = conv.get('parts', [])
        errors = conv.get('errors', [])

        reel_results = [{'file': os.path.basename(p), 'path': p, 'status': 'ok'} for p in parts]
        reel_results += [{'file': e, 'status': 'error'} for e in errors]

        _emit(job, f'🎉 Done! {len(parts)} reel parts created.', 100)
        _finish(job, f'{len(parts)} parts created in {reel_dir}',
                results=reel_results, reel_dir=reel_dir, source_video=video_path)

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
    part_secs    = int(data.get('part_duration', 60))
    show_title   = bool(data.get('show_title', True))
    show_part    = bool(data.get('show_part_label', True))
    show_wm      = bool(data.get('show_watermark', bool(watermark)))
    clip_start   = float(data.get('clip_start', 0))
    clip_end     = float(data.get('clip_end', 0))

    job_id = str(uuid.uuid4())
    job    = _make_job(job_id)

    def run():
        reel_dir = os.path.join(os.path.dirname(video_path), '_reels')
        _emit(job, f'🎬 Converting: {os.path.basename(video_path)}', 5)
        from modules.reel_converter import convert_to_reels
        conv = convert_to_reels(
            input_path=video_path, output_dir=reel_dir,
            title=title, watermark=watermark,
            part_duration_sec=part_secs,
            show_title=show_title, show_part_label=show_part, show_watermark=show_wm,
            clip_start_sec=clip_start, clip_end_sec=clip_end,
            progress_cb=lambda line: _emit(job, line),
        )
        parts  = conv.get('parts', [])
        errors = conv.get('errors', [])
        reel_results = [{'file': os.path.basename(p), 'path': p, 'status': 'ok'} for p in parts]
        reel_results += [{'file': e, 'status': 'error'} for e in errors]
        _finish(job, f'{len(parts)} parts in {reel_dir}',
                results=reel_results, reel_dir=reel_dir)

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'job_id': job_id})


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
               'max_posts_batch', 'cool_minutes', 'interval_minutes', 'enabled']
    sets  = []
    vals  = []
    for k in allowed:
        if k in data:
            sets.append(f'{k}=?')
            vals.append(data[k])

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
                elapsed  = (datetime.utcnow() - ws).total_seconds() / 60
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


# Legacy routes kept for backward compat
@app.route('/api/settings/poster/save', methods=['POST'])
def api_save_poster_settings():
    return jsonify({'ok': True, 'note': 'Use /api/poster/accounts/<id>/save instead'})

@app.route('/api/settings/poster/auth', methods=['POST'])
def api_save_poster_auth():
    return jsonify({'ok': True, 'note': 'Use /api/poster/accounts/<id>/save instead'})


# ── Boot ──────────────────────────────────────────────────────
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
    print(f'  Starting on http://localhost:5055\n')
    app.run(host='0.0.0.0', port=5055, debug=False)
