"""
Post Scheduler Daemon
======================
Runs as a background thread.
Every TICK seconds:
  1. Reads post_queue WHERE status='pending' AND scheduled_at <= NOW() (or NULL)
  2. Processes one item at a time per platform
  3. Instagram: uses instagrapi via poster_accounts credentials
  4. YouTube: uses OAuth creds from yt_uploader
  5. Respects per-account cooling (instagram accounts)
  6. Updates status → posting → done | error
"""

import os
import json
import sqlite3
import threading
import time
import subprocess
import sys
from datetime import datetime, timedelta

DB_PATH   = os.path.join(os.path.dirname(__file__), '..', 'reels_db.sqlite')
TICK      = 45   # seconds between checks

_lock     = threading.Lock()   # prevent concurrent posts to same account
_ig_clients = {}               # cache instagrapi clients per slot
_ig_import_checked = False
_ig_import_ok = False


def _db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _update(queue_id, **kwargs):
    if not kwargs:
        return
    con = _db()
    sets = ', '.join(f'{k}=?' for k in kwargs)
    vals = list(kwargs.values()) + [queue_id]
    con.execute(f'UPDATE post_queue SET {sets} WHERE id=?', vals)
    con.commit()
    con.close()


def _get_due_items():
    """Fetch pending items that are due now."""
    now = datetime.utcnow()
    con = _db()
    rows = con.execute(
        '''SELECT * FROM post_queue
           WHERE status="pending"
             AND (scheduled_at IS NULL OR scheduled_at <= ?)
           ORDER BY scheduled_at ASC, id ASC
           LIMIT 10''',
        (now.isoformat(),)
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


# ── Instagram posting ───────────────────────────────────────────

def _get_ig_account(slot_id):
    con = _db()
    row = con.execute('SELECT * FROM poster_accounts WHERE id=?', (slot_id,)).fetchone()
    con.close()
    return dict(row) if row else None


def _ig_in_cooling(account):
    """Returns True if this account is still in cooling period."""
    posts = account.get('posts_in_window', 0) or 0
    max_b = account.get('max_posts_batch', 10) or 10
    cool  = account.get('cool_minutes', 120) or 120
    ws    = account.get('window_start')
    if posts >= max_b and ws:
        try:
            ws_dt   = datetime.fromisoformat(ws)
            elapsed = (datetime.utcnow() - ws_dt).total_seconds() / 60
            if elapsed < cool:
                return True, int(cool - elapsed)
        except Exception:
            pass
    return False, 0


def _ig_post(item):
    """Post one item to Instagram. Returns (ok: bool, note: str)."""
    slot = item.get('account_slot') or 1
    acc  = _get_ig_account(slot)
    if not acc:
        return False, f'Instagram account slot {slot} not found'
    if not acc.get('enabled'):
        return False, f'Account slot {slot} is disabled'

    cooling, remaining = _ig_in_cooling(acc)
    if cooling:
        return False, f'Account slot {slot} in cooling — {remaining}m remaining'

    username = (acc.get('username') or '').strip()
    password = (acc.get('password') or '').strip()
    if not username or not password:
        return False, f'Account slot {slot} has no credentials'

    file_path = item.get('file_path', '')
    if not os.path.isfile(file_path):
        return False, f'File not found: {file_path}'

    caption = item.get('description', '')
    tags    = item.get('tags', '')
    full_caption = f"{caption}\n\n{tags}".strip() if tags else caption

    try:
        ok, err = _ensure_instagrapi()
        if not ok:
            return False, err
        from instagrapi import Client

        if slot not in _ig_clients:
            client = Client()
            session_file = os.path.join(os.path.dirname(DB_PATH), f'ig_session_{slot}.json')
            if os.path.isfile(session_file):
                try:
                    client.load_settings(session_file)
                    client.login(username, password)
                except Exception:
                    os.remove(session_file)
                    client = Client()
                    client.login(username, password)
            else:
                client.login(username, password)
                client.dump_settings(session_file)
            _ig_clients[slot] = client

        client = _ig_clients[slot]
        media  = client.clip_upload(file_path, full_caption)

        if not media:
            return False, 'clip_upload returned None'

        # Update account window counters
        con = _db()
        now = datetime.utcnow().isoformat()
        posts_in_window = (acc.get('posts_in_window') or 0) + 1
        ws = acc.get('window_start') or now
        max_b = acc.get('max_posts_batch', 10) or 10
        new_status = 'cooling' if posts_in_window >= max_b else 'idle'
        con.execute(
            '''UPDATE poster_accounts SET posts_in_window=?, window_start=?,
               last_posted_at=?, status=?, note=? WHERE id=?''',
            (posts_in_window, ws, now, new_status,
             f'Posted via queue ({posts_in_window}/{max_b})', slot)
        )
        con.commit()
        con.close()

        return True, f'Posted! media_id={media.pk}'

    except Exception as e:
        _ig_clients.pop(slot, None)  # evict on error
        return False, str(e)[:200]


def _ensure_instagrapi():
    """Ensure instagrapi is importable; try one-time auto-install if missing."""
    global _ig_import_checked, _ig_import_ok
    if _ig_import_checked:
        return _ig_import_ok, ('' if _ig_import_ok else 'instagrapi not available')

    _ig_import_checked = True
    try:
        import instagrapi  # noqa: F401
        _ig_import_ok = True
        return True, ''
    except Exception:
        pass

    try:
        cmd = [sys.executable, '-m', 'pip', 'install', 'instagrapi']
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if res.returncode != 0:
            _ig_import_ok = False
            err = (res.stderr or res.stdout or 'pip install failed').strip()[:260]
            return False, f'instagrapi install failed: {err}'
        import instagrapi  # noqa: F401
        _ig_import_ok = True
        return True, ''
    except Exception as e:
        _ig_import_ok = False
        return False, f'instagrapi missing and auto-install failed: {str(e)[:180]}'


# ── YouTube posting ─────────────────────────────────────────────

def _yt_post(item):
    """Post one item to YouTube. Returns (ok: bool, note: str)."""
    file_path = item.get('file_path', '')
    if not os.path.isfile(file_path):
        return False, f'File not found: {file_path}'

    tags_raw = item.get('tags', '')
    if tags_raw.startswith('['):
        try:
            tags = json.loads(tags_raw)
        except Exception:
            tags = [t.strip() for t in tags_raw.split(',') if t.strip()]
    else:
        tags = [t.strip() for t in tags_raw.split(',') if t.strip()]

    try:
        from modules.yt_uploader import upload_video
        result = upload_video(
            file_path     = file_path,
            title         = item.get('title', os.path.splitext(os.path.basename(file_path))[0]),
            description   = item.get('description', ''),
            tags          = tags,
            category_id   = item.get('category_id', '22'),
            privacy       = item.get('privacy', 'public'),
            thumbnail_path= item.get('thumbnail_path', ''),
        )
        if 'error' in result:
            return False, result['error']
        return True, f"YouTube: {result.get('url', '')}"
    except Exception as e:
        return False, str(e)[:200]


# ── Main loop ───────────────────────────────────────────────────

def run_post_scheduler():
    """Background scheduler daemon."""
    print('[PostScheduler] Daemon started.')
    while True:
        try:
            with _lock:
                items = _get_due_items()
                for item in items:
                    qid      = item['id']
                    platform = item.get('platform', '')

                    # Mark as posting
                    _update(qid, status='posting', note='In progress…')

                    if platform == 'instagram':
                        ok, note = _ig_post(item)
                    elif platform == 'youtube':
                        ok, note = _yt_post(item)
                    else:
                        ok, note = False, f'Unknown platform: {platform}'

                    now = datetime.utcnow().isoformat()
                    if ok:
                        _update(qid, status='done', posted_at=now, note=note)
                        print(f'[PostScheduler] ✅ QID {qid} posted ({platform})')
                    else:
                        if 'in cooling' in note.lower():
                            _update(qid, status='pending', note=f'Waiting for cooling... {note}')
                            print(f'[PostScheduler] ⏳ QID {qid} waiting on cooling: {note}')
                        else:
                            _update(qid, status='error', note=note)
                            print(f'[PostScheduler] ❌ QID {qid} failed: {note}')

                    # Small gap between items
                    time.sleep(5)

        except Exception as e:
            print(f'[PostScheduler] Daemon error: {e}')

        time.sleep(TICK)
