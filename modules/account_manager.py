"""
Multi-Profile Instagram Account Manager & Rotator
"""
import os, json, sqlite3, time
from datetime import datetime

# Adjust paths to root
BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
DB_PATH  = os.path.join(BASE_DIR, 'reels_db.sqlite')
RATE_STATE_FILE = os.path.join(BASE_DIR, '_rate_state.json')
COOKIES_DIR = os.path.join(BASE_DIR, 'downloads', 'cookies')
os.makedirs(COOKIES_DIR, exist_ok=True)

# 10 reels per 2 hours
BATCH_LIMIT = 10
WINDOW_SECONDS = 7200 

def _load_rate_state():
    if not os.path.exists(RATE_STATE_FILE):
        return {}
    try:
        with open(RATE_STATE_FILE, 'r') as f:
            return json.load(f)
    except:
        return {}

def _save_rate_state(state):
    with open(RATE_STATE_FILE, 'w') as f:
        json.dump(state, f)

def get_account_status(profile_id):
    """Get the rate limit status for a specific profile ID"""
    state = _load_rate_state()
    acc_state = state.get(profile_id, {})
    now = time.time()
    win_start = acc_state.get('window_start', 0.0)
    count = acc_state.get('count', 0)

    elapsed = now - win_start
    if elapsed >= WINDOW_SECONDS:
        # Window has expired — treat as fresh slate.
        # DO NOT write back here; record_account_usage handles the reset on next use.
        return {'in_cooldown': False, 'used': 0, 'remaining': BATCH_LIMIT,
                'window_start': 0, 'cooldown_ends': 0, 'seconds_left': 0}

    remaining = max(0, BATCH_LIMIT - count)
    cooldown_ends = win_start + WINDOW_SECONDS
    return {'in_cooldown': count >= BATCH_LIMIT, 'used': count, 'remaining': remaining,
            'window_start': win_start, 'cooldown_ends': cooldown_ends,
            'seconds_left': max(0, int(cooldown_ends - now))}

def record_account_usage(profile_id, count=1):
    state = _load_rate_state()
    acc_state = state.get(profile_id, {})
    now = time.time()

    if now - acc_state.get('window_start', 0.0) >= WINDOW_SECONDS:
        # Window expired — start a fresh window right now and persist it
        acc_state['window_start'] = now
        acc_state['count'] = 0

    acc_state['count'] = acc_state.get('count', 0) + count
    state[profile_id] = acc_state
    _save_rate_state(state)   # always persist after every usage

def get_active_profile(purpose='download'):
    """
    Scans the DB ordered by priority to find the first active, non-failing profile
    that has available rate limit quota.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Retrieve all active accounts sorted by priority ASC (1 is highest priority)
    accounts = conn.execute("SELECT * FROM ig_accounts WHERE is_active=1 ORDER BY priority ASC").fetchall()
    conn.close()
    
    for acc in accounts:
        # Check if error count is too high
        if acc['error_count'] > 3:
            continue
            
        # Check if cookie file physically exists
        cookie_path = os.path.join(BASE_DIR, acc['cookie_path']) if acc['cookie_path'] else ''
        if not cookie_path or not os.path.isfile(cookie_path):
            continue
            
        # Check rate limits
        status = get_account_status(acc['id'])
        if status['in_cooldown']:
            continue
            
        return {'id': acc['id'], 'label': acc['label'], 'cookie_path': cookie_path}
        
    return None

def mark_profile_failed(profile_id):
    """Increments error count. If it exceeds 3, the profile is essentially sidelined."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE ig_accounts SET error_count = error_count + 1 WHERE id=?", (profile_id,))
    conn.commit()
    conn.close()

def ensure_default_profiles_exist():
    """Ensure p1 to p5 records exist in DB on boot."""
    conn = sqlite3.connect(DB_PATH)
    for i in range(1, 6):
        pid = f"p{i}"
        exists = conn.execute("SELECT 1 FROM ig_accounts WHERE id=?", (pid,)).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO ig_accounts (id, label, priority, is_active) VALUES (?, ?, ?, 0)",
                (pid, f"Profile {i}", i)
            )
    conn.commit()
    conn.close()
