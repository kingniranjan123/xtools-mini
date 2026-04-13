"""
Multi-Account Instagram Auto-Poster Daemon
==========================================
Up to 5 accounts each with:
  - their own source folder
  - per-account caption / tags
  - max_posts_batch (default 10) before a cooling period
  - cool_minutes (default 120) cooling window
  - interval_minutes gap between individual posts

Posting order:
  Round-robin across enabled accounts.
  For each account:
    1. Check if in cooling period → skip
    2. Pull next unposted video from account's folder
    3. Post it via instagrapi
    4. Increment posts_in_window; if >= max_posts_batch start cooling
    5. Sleep interval_minutes before next post for that account

The daemon wakes every TICK_SECONDS and finds the next eligible account.
"""

import os
import glob
import sqlite3
import threading
import time
from datetime import datetime, timedelta

try:
    from instagrapi import Client
    HAS_INSTAGRAPI = True
except ImportError:
    HAS_INSTAGRAPI = False

TICK_SECONDS = 30          # how often the daemon checks state
VIDEO_EXTS   = ('.mp4', '.mov', '.m4v', '.avi', '.mkv')
DB_PATH      = os.path.join(os.path.dirname(__file__), '..', 'reels_db.sqlite')


# ── DB helpers (thread-safe direct sqlite3, not Flask g) ─────────

def _db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _get_accounts():
    con = _db()
    rows = con.execute('SELECT * FROM poster_accounts ORDER BY id').fetchall()
    con.close()
    return [dict(r) for r in rows]


def _update_account(account_id, **kwargs):
    if not kwargs:
        return
    sets = ', '.join(f'{k}=?' for k in kwargs)
    vals = list(kwargs.values()) + [account_id]
    con = _db()
    con.execute(f'UPDATE poster_accounts SET {sets} WHERE id=?', vals)
    con.commit()
    con.close()


def _log(account_id, file_path, outcome, note=''):
    con = _db()
    con.execute(
        'INSERT INTO poster_log (account_id, file_path, outcome, note) VALUES (?,?,?,?)',
        (account_id, file_path, outcome, note)
    )
    con.commit()
    con.close()


# ── Folder scanning ──────────────────────────────────────────────

def _already_posted(file_path, account_id):
    """Check poster_log to avoid reposting the same file."""
    con = _db()
    row = con.execute(
        "SELECT id FROM poster_log WHERE account_id=? AND file_path=? AND outcome='posted'",
        (account_id, file_path)
    ).fetchone()
    con.close()
    return row is not None


def _next_video(folder_path, account_id):
    """Return path to the oldest unposted video in the account's folder."""
    if not folder_path or not os.path.isdir(folder_path):
        return None
    # Collect all videos, sorted by modification time (oldest first)
    files = []
    for ext in VIDEO_EXTS:
        files.extend(glob.glob(os.path.join(folder_path, f'*{ext}')))
        files.extend(glob.glob(os.path.join(folder_path, f'*{ext.upper()}')))
    files = sorted(set(files), key=lambda f: os.path.getmtime(f))
    for f in files:
        if not _already_posted(f, account_id):
            return f
    return None


# ── Instagrapi session cache (one Client per account to avoid re-login) ──

_clients = {}   # account_id → Client


def _get_client(account):
    acc_id   = account['id']
    username = (account['username'] or '').strip()
    password = (account['password'] or '').strip()
    if not username or not password:
        raise ValueError('Username or password not configured.')

    if acc_id in _clients:
        return _clients[acc_id]

    if not HAS_INSTAGRAPI:
        raise RuntimeError('instagrapi not installed. Run: pip install instagrapi')

    client = Client()
    # Session file path to persist login
    session_file = os.path.join(os.path.dirname(__file__), '..', f'ig_session_{acc_id}.json')
    try:
        if os.path.isfile(session_file):
            client.load_settings(session_file)
            client.login(username, password)
        else:
            client.login(username, password)
            client.dump_settings(session_file)
    except Exception as e:
        # Clear bad session and retry fresh
        if os.path.isfile(session_file):
            os.remove(session_file)
        client = Client()
        client.login(username, password)
        client.dump_settings(session_file)

    _clients[acc_id] = client
    return client


def _evict_client(account_id):
    _clients.pop(account_id, None)


# ── Posting logic for one account ────────────────────────────────

def _post_one(account):
    acc_id = account['id']
    tag    = f'[Account {acc_id} · {account["username"]}]'

    # 1. Check enabled
    if not account['enabled']:
        return 'disabled', 'Account not enabled', False

    # 2. Check cooling period
    posts_in_window = account['posts_in_window'] or 0
    max_batch       = account['max_posts_batch'] or 10
    cool_minutes    = account['cool_minutes'] or 120
    window_start    = account['window_start']

    if window_start:
        try:
            ws_dt = datetime.fromisoformat(window_start)
        except Exception:
            ws_dt = None
        if ws_dt:
            elapsed = (datetime.utcnow() - ws_dt).total_seconds() / 60
            if posts_in_window >= max_batch:
                if elapsed < cool_minutes:
                    remaining = int(cool_minutes - elapsed)
                    _update_account(acc_id, status='cooling',
                                    note=f'Cooling — {remaining}m remaining')
                    return 'cooling', f'{remaining}m remaining', False
                else:
                    # Window expired — reset
                    posts_in_window = 0
                    _update_account(acc_id, posts_in_window=0, window_start=None,
                                    status='idle', note='Window reset')

    # 3. Find next video
    video_path = _next_video(account['folder_path'], acc_id)
    if not video_path:
        _update_account(acc_id, status='idle', note='No unposted videos in folder')
        return 'no_video', 'No unposted videos', False

    # 4. Post it
    caption_text = account['caption'] or ''
    tags_text    = account['tags'] or ''
    full_caption = f"{caption_text}\n\n{tags_text}".strip()

    print(f'{tag} Posting: {os.path.basename(video_path)}')
    _update_account(acc_id, status='posting', note=f'Uploading {os.path.basename(video_path)}')

    try:
        client = _get_client(account)
        media  = client.clip_upload(video_path, full_caption)
        if not media:
            raise RuntimeError('clip_upload returned None')

        # Success
        now = datetime.utcnow().isoformat()
        posts_in_window += 1
        ws  = account['window_start'] or now
        _update_account(acc_id,
                         posts_in_window=posts_in_window,
                         window_start=ws,
                         last_posted_at=now,
                         status='idle' if posts_in_window < max_batch else 'cooling',
                         note=f'Posted {os.path.basename(video_path)} ({posts_in_window}/{max_batch})')
        _log(acc_id, video_path, 'posted', f'media_id={media.pk}')
        print(f'{tag} ✓ Posted ({posts_in_window}/{max_batch})')
        return 'posted', os.path.basename(video_path), True

    except Exception as e:
        _evict_client(acc_id)
        err = str(e)[:200]
        _update_account(acc_id, status='error', note=err)
        _log(acc_id, video_path, 'error', err)
        print(f'{tag} ✗ Error: {err}')
        return 'error', err, False


# ── Main Daemon ───────────────────────────────────────────────────

def run_poster_daemon(app_context_fetcher=None):
    """
    Multi-account round-robin auto-poster daemon.
    app_context_fetcher is kept for backwards-compat but not required
    (daemon reads directly from SQLite).
    """
    print('[Multi-Poster] Daemon started.')
    # Pointer into the round-robin
    current_slot = 0

    while True:
        try:
            accounts = _get_accounts()
            enabled  = [a for a in accounts if a['enabled']]

            if not enabled:
                time.sleep(TICK_SECONDS)
                continue

            # Select next account in round-robin
            current_slot = current_slot % len(enabled)
            account      = enabled[current_slot]
            current_slot = (current_slot + 1) % len(enabled)

            outcome, note, did_post = _post_one(account)

            # If we just posted, wait the per-account interval before next tick
            if did_post:
                interval_mins = account.get('interval_minutes') or 15
                wait_secs = interval_mins * 60
                print(f'[Multi-Poster] Waiting {interval_mins}m before next post...')
                time.sleep(wait_secs)
            else:
                time.sleep(TICK_SECONDS)

        except Exception as e:
            print(f'[Multi-Poster] Daemon error: {e}')
            time.sleep(TICK_SECONDS)
