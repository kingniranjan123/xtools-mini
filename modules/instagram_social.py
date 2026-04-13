"""
Instagram Social Tools — followers/following extraction + batch follow/unfollow
Rate limiter: 10 users per 2-hour window, with cooling period enforcement.
Cooldown state is persisted to a JSON file so it survives server restarts.
"""
import os, re, json, time, datetime, requests
from http.cookiejar import MozillaCookieJar

# ────────────────────────────────────────────────────────────────
#  Cooldown configuration
# ────────────────────────────────────────────────────────────────
BATCH_LIMIT     = 10          # max follows per window
WINDOW_SECONDS  = 7200        # 2 hours in seconds

_STATE_FILE = os.path.join(os.path.dirname(__file__), '..', '_rate_state.json')

def _load_state() -> dict:
    """Load persisted rate-limit state from disk."""
    try:
        if os.path.isfile(_STATE_FILE):
            with open(_STATE_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {'follow_count': 0, 'window_start': 0.0,
            'unfollow_count': 0, 'unfollow_window_start': 0.0}

def _save_state(state: dict):
    """Persist rate-limit state to disk."""
    try:
        with open(os.path.normpath(_STATE_FILE), 'w') as f:
            json.dump(state, f)
    except Exception:
        pass

def get_follow_status() -> dict:
    """
    Returns the current batch status for follow actions.
    {
      in_cooldown: bool,
      used: int,
      remaining: int,
      window_start: float (epoch),
      cooldown_ends: float (epoch),   # when the 2-hr window expires
      seconds_left: int               # seconds until cooldown lifts
    }
    """
    state     = _load_state()
    now       = time.time()
    win_start = state.get('follow_window_start', 0.0)
    count     = state.get('follow_count', 0)
    elapsed   = now - win_start

    if elapsed >= WINDOW_SECONDS:
        # Window expired — reset
        return {
            'in_cooldown': False,
            'used': 0,
            'remaining': BATCH_LIMIT,
            'window_start': now,
            'cooldown_ends': now + WINDOW_SECONDS,
            'seconds_left': 0,
        }

    remaining = max(0, BATCH_LIMIT - count)
    in_cooldown = count >= BATCH_LIMIT
    cooldown_ends = win_start + WINDOW_SECONDS
    seconds_left  = max(0, int(cooldown_ends - now))

    return {
        'in_cooldown':  in_cooldown,
        'used':         count,
        'remaining':    remaining,
        'window_start': win_start,
        'cooldown_ends': cooldown_ends,
        'seconds_left': seconds_left,
    }

def get_unfollow_status() -> dict:
    """Same as follow status but for the unfollow action."""
    state     = _load_state()
    now       = time.time()
    win_start = state.get('unfollow_window_start', 0.0)
    count     = state.get('unfollow_count', 0)
    elapsed   = now - win_start

    if elapsed >= WINDOW_SECONDS:
        return {
            'in_cooldown': False,
            'used': 0,
            'remaining': BATCH_LIMIT,
            'window_start': now,
            'cooldown_ends': now + WINDOW_SECONDS,
            'seconds_left': 0,
        }

    remaining = max(0, BATCH_LIMIT - count)
    in_cooldown = count >= BATCH_LIMIT
    cooldown_ends = win_start + WINDOW_SECONDS
    seconds_left  = max(0, int(cooldown_ends - now))

    return {
        'in_cooldown':  in_cooldown,
        'used':         count,
        'remaining':    remaining,
        'window_start': win_start,
        'cooldown_ends': cooldown_ends,
        'seconds_left': seconds_left,
    }

def _record_follows(n: int):
    """Increment the follow counter, starting a new window if needed."""
    state = _load_state()
    now   = time.time()
    elapsed = now - state.get('follow_window_start', 0.0)
    if elapsed >= WINDOW_SECONDS:
        state['follow_window_start'] = now
        state['follow_count']        = 0
    state['follow_count'] = state.get('follow_count', 0) + n
    _save_state(state)

def _record_unfollows(n: int):
    """Increment the unfollow counter, starting a new window if needed."""
    state = _load_state()
    now   = time.time()
    elapsed = now - state.get('unfollow_window_start', 0.0)
    if elapsed >= WINDOW_SECONDS:
        state['unfollow_window_start'] = now
        state['unfollow_count']        = 0
    state['unfollow_count'] = state.get('unfollow_count', 0) + n
    _save_state(state)


# ── Shared session builder ────────────────────────────────────

def _build_session(cookies_file: str) -> requests.Session:
    """Load cookies.txt and return an authenticated requests.Session."""
    s = requests.Session()
    s.headers.update({
        'User-Agent':       'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept':           '*/*',
        'Accept-Language':  'en-US,en;q=0.9',
        'X-IG-App-ID':      '936619743392459',
        'X-Requested-With': 'XMLHttpRequest',
        'Referer':          'https://www.instagram.com/',
        'Origin':           'https://www.instagram.com',
    })
    if cookies_file and os.path.isfile(cookies_file):
        cj = MozillaCookieJar()
        cj.load(cookies_file, ignore_discard=True, ignore_expires=True)
        s.cookies = requests.utils.cookiejar_from_dict(
            {c.name: c.value for c in cj}
        )
        csrf = s.cookies.get('csrftoken', '')
        if csrf:
            s.headers['X-CSRFToken'] = csrf
    return s


# ── User lookup ───────────────────────────────────────────────

def lookup_user(username: str, cookies_file: str = None) -> dict:
    from modules.account_manager import get_active_profile, mark_profile_failed
    
    # Try up to 5 profiles if they keep hitting 429
    profile = get_active_profile()
    if not profile and not cookies_file:
         return {'error': 'No active Instagram profiles configured or all are rate-limited'}
         
    attempt_count = 0
    while True:
        target_cookie = cookies_file or profile['cookie_path']
        s = _build_session(target_cookie)
        
        try:
            url  = f'https://www.instagram.com/api/v1/users/web_profile_info/?username={username}'
            resp = s.get(url, timeout=12)
            resp.raise_for_status()
            data = resp.json()
            user = data.get('data', {}).get('user', {})
            if not user:
                # Instagram might send empty user if not found
                return {'error': 'User not found or private'}
                
            timeline = user.get('edge_owner_to_timeline_media', {}).get('edges', [])
            shortcodes = [edge['node']['shortcode'] for edge in timeline if 'node' in edge and 'shortcode' in edge['node']]
            return {
                'user_id':         user.get('id'),
                'username':        user.get('username', username),
                'full_name':       user.get('full_name', ''),
                'followers_count': user.get('edge_followed_by', {}).get('count', 0),
                'following_count': user.get('edge_follow', {}).get('count', 0),
                'is_private':      user.get('is_private', False),
                'profile_pic_url': user.get('profile_pic_url', ''),
                'recent_posts':    shortcodes,
                'used_profile':    profile['label'] if profile else 'legacy',
                'profile_id':      profile['id'] if profile else 'none'
            }
            
        except requests.exceptions.HTTPError as exc:
            if resp.status_code == 429:
                # Blocked! Mark taking profile as failed.
                if profile:
                    mark_profile_failed(profile['id'])
                    attempt_count += 1
                    # Rotate to next priority
                    profile = get_active_profile()
                    if profile and attempt_count < 5:
                        continue # Try again with new profile!
                        
                return {'error': 'Instagram rate-limit (429) hit and no alternative profiles are left.'}
            return {'error': f'HTTP Error {resp.status_code}: {exc}'}
        except Exception as exc:
            return {'error': str(exc)}


# ── Followers / Following extractor ───────────────────────────

def extract_followers(username: str, cookies_file: str, max_users: int = 500,
                      list_type: str = 'followers', progress_cb=None) -> list:
    s    = _build_session(cookies_file)
    info = lookup_user(username, cookies_file)
    if 'error' in info:
        if progress_cb: progress_cb(f'Error: {info["error"]}')
        return []

    user_id = info['user_id']
    results = []
    types_to_fetch = ['followers', 'following'] if list_type == 'both' else [list_type]

    for ltype in types_to_fetch:
        if progress_cb:
            progress_cb(f'Extracting {ltype} for @{username}...')
        fetched = _paginate_list(s, user_id, ltype, max_users, progress_cb)
        for u in fetched:
            u['list_type'] = ltype
        results.extend(fetched)

    return results


def _paginate_list(session, user_id, ltype, max_count, progress_cb):
    endpoint = {
        'followers': f'https://www.instagram.com/api/v1/friendships/{user_id}/followers/',
        'following': f'https://www.instagram.com/api/v1/friendships/{user_id}/following/',
    }[ltype]

    users    = []
    next_max = None
    page     = 0

    while len(users) < max_count:
        page += 1
        params = {'count': 50}
        if next_max:
            params['max_id'] = next_max

        try:
            resp = session.get(endpoint, params=params, timeout=20)
            if resp.status_code == 401:
                if progress_cb: progress_cb('401 Unauthorized - cookies expired or invalid')
                break
            if resp.status_code == 429:
                if progress_cb: progress_cb('Rate limited by Instagram - waiting 30s...')
                time.sleep(30)
                continue
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            if progress_cb: progress_cb(f'Error on page {page}: {e}')
            break

        batch = data.get('users', [])
        for u in batch:
            users.append({
                'username':        u.get('username', ''),
                'full_name':       u.get('full_name', ''),
                'user_id':         u.get('pk', ''),
                'followers_count': u.get('follower_count'),
                'is_private':      u.get('is_private', False),
                'profile_pic_url': u.get('profile_pic_url', ''),
            })

        if progress_cb:
            progress_cb(f'Page {page}: +{len(batch)} users - total {len(users)}')

        next_max = data.get('next_max_id')
        if not next_max or not batch:
            break
        if len(users) >= max_count:
            break
        time.sleep(1.5)

    return users[:max_count]


# ── Batch Follow ──────────────────────────────────────────────

def follow_users(usernames: list, cookies_file: str,
                 skip_existing: bool = True, skip_private: bool = False,
                 progress_cb=None) -> dict:
    """
    Follow up to (remaining) users from the 2-hour batch window.
    Returns { followed, skipped, errors, cooldown_seconds_left }
    """
    status = get_follow_status()

    if status['in_cooldown']:
        msg = (f'Cooling period active. '
               f'{_fmt_duration(status["seconds_left"])} remaining until next batch.')
        if progress_cb: progress_cb(msg)
        return {
            'followed': 0, 'skipped': 0, 'errors': 0,
            'in_cooldown': True,
            'seconds_left': status['seconds_left'],
            'error': msg,
        }

    to_process = usernames[:status['remaining']]
    s          = _build_session(cookies_file)
    followed   = 0
    skipped    = 0
    errors_    = 0

    if progress_cb:
        prog_str = f'Batch follow: {len(to_process)} users queued ({status["used"]}/{BATCH_LIMIT} used in this 2-hour window)'
        progress_cb(prog_str)

    for i, username in enumerate(to_process):
        # Re-check status mid-batch
        current = get_follow_status()
        if current['in_cooldown']:
            if progress_cb:
                progress_cb(f'Batch limit reached at {i} follows. Cooling period started.')
            break

        if progress_cb:
            progress_cb(f'[{i+1}/{len(to_process)}] Following @{username}...')

        try:
            info = lookup_user(username, cookies_file)
            if 'error' in info:
                if progress_cb: progress_cb(f'  Could not find @{username}: {info["error"]}')
                errors_ += 1
                continue

            if skip_private and info.get('is_private'):
                if progress_cb: progress_cb(f'  Skipped @{username} (private account)')
                skipped += 1
                continue

            user_id = info['user_id']
            resp = s.post(
                f'https://www.instagram.com/api/v1/friendships/create/{user_id}/',
                data={'user_id': user_id},
                timeout=15
            )

            if resp.status_code == 200:
                _record_follows(1)
                followed += 1
                new_status = get_follow_status()
                if progress_cb:
                    progress_cb(f'  Followed @{username} ({new_status["used"]}/{BATCH_LIMIT} in 2hr window)')
            elif resp.status_code == 400:
                msg = resp.json().get('message', 'Already following or blocked')
                if progress_cb: progress_cb(f'  Skipped @{username}: {msg}')
                skipped += 1
            elif resp.status_code == 429:
                if progress_cb: progress_cb('  Rate limited by Instagram - stopping batch')
                break
            else:
                if progress_cb: progress_cb(f'  Error {resp.status_code} for @{username}')
                errors_ += 1

        except Exception as exc:
            if progress_cb: progress_cb(f'  Exception for @{username}: {exc}')
            errors_ += 1

        if i < len(to_process) - 1:
            time.sleep(12)  # ~12s gap between follows for safety

    final_status = get_follow_status()
    return {
        'followed':       followed,
        'skipped':        skipped,
        'errors':         errors_,
        'in_cooldown':    final_status['in_cooldown'],
        'seconds_left':   final_status['seconds_left'],
        'used':           final_status['used'],
        'remaining':      final_status['remaining'],
    }


# ── Batch Unfollow ────────────────────────────────────────────

def unfollow_users(usernames: list, cookies_file: str, progress_cb=None) -> dict:
    """
    Unfollow up to 10 users per 2-hour window.
    Returns { unfollowed, skipped, errors, in_cooldown, seconds_left }
    """
    status = get_unfollow_status()

    if status['in_cooldown']:
        msg = (f'Cooling period active. '
               f'{_fmt_duration(status["seconds_left"])} remaining until next unfollow batch.')
        if progress_cb: progress_cb(msg)
        return {
            'unfollowed': 0, 'skipped': 0, 'errors': 0,
            'in_cooldown': True,
            'seconds_left': status['seconds_left'],
            'error': msg,
        }

    to_process = usernames[:status['remaining']]
    s          = _build_session(cookies_file)
    unfollowed = 0
    skipped    = 0
    errors_    = 0

    if progress_cb:
        progress_cb(f'Batch unfollow: {len(to_process)} users queued ({status["used"]}/{BATCH_LIMIT} used in this 2-hour window)')

    for i, username in enumerate(to_process):
        current = get_unfollow_status()
        if current['in_cooldown']:
            if progress_cb: progress_cb(f'Batch limit reached. Cooling period started.')
            break

        if progress_cb:
            progress_cb(f'[{i+1}/{len(to_process)}] Unfollowing @{username}...')

        try:
            info = lookup_user(username, cookies_file)
            if 'error' in info:
                if progress_cb: progress_cb(f'  Could not find @{username}: {info["error"]}')
                errors_ += 1
                continue

            user_id = info['user_id']
            resp = s.post(
                f'https://www.instagram.com/api/v1/friendships/destroy/{user_id}/',
                data={'user_id': user_id},
                timeout=15
            )

            if resp.status_code == 200:
                _record_unfollows(1)
                new_status = get_unfollow_status()
                unfollowed += 1
                if progress_cb:
                    progress_cb(f'  Unfollowed @{username} ({new_status["used"]}/{BATCH_LIMIT} in 2hr window)')
            elif resp.status_code == 400:
                msg = resp.json().get('message', 'Not following or blocked')
                if progress_cb: progress_cb(f'  Skipped @{username}: {msg}')
                skipped += 1
            elif resp.status_code == 429:
                if progress_cb: progress_cb('  Rate limited by Instagram - stopping')
                break
            else:
                if progress_cb: progress_cb(f'  Error {resp.status_code} for @{username}')
                errors_ += 1

        except Exception as exc:
            if progress_cb: progress_cb(f'  Exception for @{username}: {exc}')
            errors_ += 1

        if i < len(to_process) - 1:
            time.sleep(12)

    final_status = get_unfollow_status()
    return {
        'unfollowed':   unfollowed,
        'skipped':      skipped,
        'errors':       errors_,
        'in_cooldown':  final_status['in_cooldown'],
        'seconds_left': final_status['seconds_left'],
        'used':         final_status['used'],
        'remaining':    final_status['remaining'],
    }


# ── Helpers ───────────────────────────────────────────────────

def _fmt_duration(seconds: int) -> str:
    """Format seconds into 'Xh Ym' or 'Xm Ys' string."""
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f'{h}h {m:02d}m'
    if m > 0:
        return f'{m}m {s:02d}s'
    return f'{s}s'
