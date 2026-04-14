"""
YouTube Uploader — OAuth 2.0 + YouTube Data API v3
====================================================
Setup (one-time):
1. Go to Google Cloud Console → Create project
2. Enable YouTube Data API v3
3. Create OAuth 2.0 credentials → Desktop App type
4. Download client_secret.json → paste contents in Settings → YouTube OAuth
5. Click "Authorize YouTube" button → browser opens for one-time consent
6. After consent the refresh_token is stored locally forever

Upload is then fully automated via refresh_token.
"""
import os
import json
import sqlite3
import pickle
import tempfile

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'reels_db.sqlite')

SCOPES = ['https://www.googleapis.com/auth/youtube.upload',
          'https://www.googleapis.com/auth/youtube.readonly']

YT_CATEGORIES = {
    '1':  'Film & Animation', '2': 'Autos & Vehicles', '10': 'Music',
    '15': 'Pets & Animals',  '17': 'Sports','19': 'Travel & Events',
    '20': 'Gaming', '22': 'People & Blogs', '23': 'Comedy',
    '24': 'Entertainment', '25': 'News & Politics', '26': 'Howto & Style',
    '27': 'Education', '28': 'Science & Technology', '29': 'Nonprofits & Activism',
}


def _db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _read_setting(key, default=''):
    con = _db()
    row = con.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    con.close()
    return row['value'] if row else default


def _save_setting(key, value):
    con = _db()
    con.execute('INSERT INTO settings (key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))
    con.commit()
    con.close()


# ── Credential helpers ────────────────────────────────────────────

def get_credentials():
    """Return valid google.oauth2.credentials.Credentials or None."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError:
        return None

    token_json = _read_setting('yt_oauth_token', '')
    if not token_json:
        return None

    try:
        token_data = json.loads(token_json)
        creds = Credentials(
            token          = token_data.get('token'),
            refresh_token  = token_data.get('refresh_token'),
            token_uri      = token_data.get('token_uri', 'https://oauth2.googleapis.com/token'),
            client_id      = token_data.get('client_id'),
            client_secret  = token_data.get('client_secret'),
            scopes         = SCOPES,
        )
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            _save_credentials(creds)
        return creds
    except Exception as e:
        print(f'[YT-OAuth] Credential load error: {e}')
        return None


def _save_credentials(creds):
    token_data = {
        'token':        creds.token,
        'refresh_token': creds.refresh_token,
        'token_uri':    creds.token_uri,
        'client_id':    creds.client_id,
        'client_secret': creds.client_secret,
    }
    _save_setting('yt_oauth_token', json.dumps(token_data))


def is_authorized():
    """Check if we have valid YouTube OAuth credentials."""
    creds = get_credentials()
    return creds is not None and creds.valid


def get_auth_url(client_secret_json: str, redirect_uri: str) -> str:
    """Generate OAuth auth URL. Returns URL string."""
    try:
        from google_auth_oauthlib.flow import Flow
    except ImportError:
        raise RuntimeError('google-auth-oauthlib not installed. pip install google-auth-oauthlib google-api-python-client')

    client_config = json.loads(client_secret_json)
    # Accept both 'web' and 'installed'
    if 'web' not in client_config and 'installed' not in client_config:
        client_config = {'installed': client_config}

    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=redirect_uri,
    )
    auth_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent',
    )
    # Save state + client config + redirect for callback
    _save_setting('yt_oauth_flow_state', state)
    _save_setting('yt_oauth_client_config', json.dumps(client_config))
    _save_setting('yt_oauth_redirect_uri', redirect_uri)
    return auth_url


def exchange_code(code: str, state: str) -> bool:
    """Exchange auth code for tokens. Returns True on success."""
    try:
        from google_auth_oauthlib.flow import Flow
    except ImportError:
        return False

    stored_state  = _read_setting('yt_oauth_flow_state')
    client_config = json.loads(_read_setting('yt_oauth_client_config', '{}'))
    redirect_uri  = _read_setting('yt_oauth_redirect_uri', '')

    if not client_config:
        return False

    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        state=stored_state,
        redirect_uri=redirect_uri,
    )
    flow.fetch_token(code=code)
    _save_credentials(flow.credentials)
    return True


def get_channel_info() -> dict:
    """Return basic channel info for the authorized account."""
    try:
        from googleapiclient.discovery import build
        from google.auth.transport.requests import Request
    except ImportError:
        return {'error': 'google-api-python-client not installed'}

    creds = get_credentials()
    if not creds:
        return {'error': 'Not authorized'}

    try:
        youtube = build('youtube', 'v3', credentials=creds)
        resp = youtube.channels().list(part='snippet,statistics', mine=True).execute()
        items = resp.get('items', [])
        if not items:
            return {'error': 'No channel found'}
        ch = items[0]
        return {
            'channel_id':   ch['id'],
            'title':        ch['snippet']['title'],
            'subscriber_count': ch['statistics'].get('subscriberCount', '?'),
            'video_count':  ch['statistics'].get('videoCount', '?'),
        }
    except Exception as e:
        return {'error': str(e)}


# ── Upload ─────────────────────────────────────────────────────────

def upload_video(file_path: str, title: str, description: str,
                 tags: list, category_id: str = '22',
                 privacy: str = 'public',
                 thumbnail_path: str = '',
                 progress_cb=None) -> dict:
    """
    Upload a video to YouTube. Returns {'video_id': ..., 'url': ...} or {'error': ...}
    """
    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        from googleapiclient.errors import HttpError
    except ImportError:
        return {'error': 'google-api-python-client not installed. Run: pip install google-api-python-client google-auth-oauthlib'}

    if not os.path.isfile(file_path):
        return {'error': f'File not found: {file_path}'}

    creds = get_credentials()
    if not creds:
        return {'error': 'YouTube not authorized. Go to Settings → YouTube OAuth.'}

    youtube = build('youtube', 'v3', credentials=creds)

    body = {
        'snippet': {
            'title':       title[:100],
            'description': description[:5000],
            'tags':        tags[:500],
            'categoryId':  category_id,
        },
        'status': {
            'privacyStatus': privacy,
            'selfDeclaredMadeForKids': False,
        }
    }

    if progress_cb:
        progress_cb(f'📤 Uploading to YouTube: {os.path.basename(file_path)}')

    try:
        media = MediaFileUpload(file_path, mimetype='video/*', resumable=True, chunksize=4 * 1024 * 1024)
        req   = youtube.videos().insert(part=','.join(body.keys()), body=body, media_body=media)

        response = None
        while response is None:
            status, response = req.next_chunk()
            if status and progress_cb:
                pct = int(status.progress() * 100)
                progress_cb(f'  Upload progress: {pct}%')

        video_id = response.get('id')
        url = f'https://www.youtube.com/watch?v={video_id}'

        # Upload thumbnail if provided
        if thumbnail_path and os.path.isfile(thumbnail_path):
            try:
                youtube.thumbnails().set(
                    videoId=video_id,
                    media_body=MediaFileUpload(thumbnail_path, mimetype='image/jpeg')
                ).execute()
                if progress_cb: progress_cb('  ✅ Thumbnail uploaded')
            except Exception as te:
                if progress_cb: progress_cb(f'  ⚠ Thumbnail failed: {te}')

        if progress_cb:
            progress_cb(f'  ✅ Uploaded: {url}')

        return {'video_id': video_id, 'url': url, 'title': title}

    except Exception as e:
        return {'error': str(e)[:300]}
