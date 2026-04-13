"""
Channel Analytics Module
Extracts statistics from YouTube (via YouTube Data API v3)
and Instagram (via instagrapi) for any public channel/profile.
"""
import os
import json
import re
from datetime import datetime, timezone
from collections import defaultdict

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


# ─────────────────────────────────────────────────────────────
#  YouTube Analytics
# ─────────────────────────────────────────────────────────────

YT_BASE = 'https://www.googleapis.com/youtube/v3'


def _yt_get(endpoint, params, api_key):
    """Make a YouTube Data API v3 GET request."""
    params['key'] = api_key
    resp = requests.get(f'{YT_BASE}/{endpoint}', params=params, timeout=10)
    if resp.status_code == 403:
        raise ValueError('YouTube API key is invalid or quota exceeded.')
    if resp.status_code != 200:
        raise RuntimeError(f'YouTube API error {resp.status_code}: {resp.text[:200]}')
    return resp.json()


def _resolve_channel_id(input_str, api_key):
    """
    Resolve a channel URL / handle / ID to a channel ID.
    Supports: @handle, channel ID (UCxxx), or full URL.
    """
    # Extract handle / ID from URL if needed
    handle_match = re.search(r'@([\w.]+)', input_str)
    ucid_match = re.search(r'UC[\w-]{22}', input_str)

    if ucid_match:
        return ucid_match.group(0)

    if handle_match:
        handle = handle_match.group(1)
    else:
        # Treat as bare handle without @
        handle = input_str.lstrip('@').strip()

    data = _yt_get('channels', {
        'part': 'id',
        'forHandle': f'@{handle}',
        'maxResults': 1
    }, api_key)

    items = data.get('items', [])
    if not items:
        raise ValueError(f'No YouTube channel found for: @{handle}')
    return items[0]['id']


def get_youtube_channel_stats(channel_input: str, api_key: str) -> dict:
    """
    Full analytics for a YouTube channel.
    Returns a dict with channel_info, videos, best_times, engagement_summary.
    """
    if not HAS_REQUESTS:
        raise RuntimeError('requests library not installed.')
    if not api_key or len(api_key) < 10:
        raise ValueError('No YouTube API key configured. Go to Settings → API Keys.')

    channel_id = _resolve_channel_id(channel_input, api_key)

    # ── Channel metadata ─────────────────────────────────────
    ch_data = _yt_get('channels', {
        'part': 'snippet,statistics,contentDetails',
        'id': channel_id
    }, api_key)

    if not ch_data.get('items'):
        raise ValueError('Channel not found.')

    ch = ch_data['items'][0]
    snippet = ch['snippet']
    stats = ch['statistics']
    uploads_playlist = ch['contentDetails']['relatedPlaylists']['uploads']

    channel_info = {
        'id': channel_id,
        'title': snippet.get('title'),
        'description': snippet.get('description', '')[:300],
        'thumbnail': snippet.get('thumbnails', {}).get('high', {}).get('url'),
        'published_at': snippet.get('publishedAt'),
        'country': snippet.get('country', '—'),
        'subscribers': int(stats.get('subscriberCount', 0)),
        'total_views': int(stats.get('viewCount', 0)),
        'video_count': int(stats.get('videoCount', 0)),
        'hidden_subscribers': stats.get('hiddenSubscriberCount', False),
    }

    # ── Fetch up to 50 video IDs from uploads playlist ───────
    video_ids = []
    page_token = None
    while len(video_ids) < 50:
        params = {
            'part': 'contentDetails',
            'playlistId': uploads_playlist,
            'maxResults': 50
        }
        if page_token:
            params['pageToken'] = page_token

        pl_data = _yt_get('playlistItems', params, api_key)
        for item in pl_data.get('items', []):
            vid_id = item['contentDetails'].get('videoId')
            if vid_id:
                video_ids.append(vid_id)
        page_token = pl_data.get('nextPageToken')
        if not page_token or len(video_ids) >= 50:
            break

    # ── Fetch video stats in batches of 50 ───────────────────
    videos = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i+50]
        vid_data = _yt_get('videos', {
            'part': 'snippet,statistics,contentDetails',
            'id': ','.join(batch)
        }, api_key)

        for v in vid_data.get('items', []):
            vs = v.get('statistics', {})
            vsnip = v.get('snippet', {})
            views = int(vs.get('viewCount', 0))
            likes = int(vs.get('likeCount', 0))
            comments = int(vs.get('commentCount', 0))
            eng_rate = round((likes + comments) / max(views, 1) * 100, 3)

            pub = vsnip.get('publishedAt', '')
            try:
                dt = datetime.fromisoformat(pub.replace('Z', '+00:00'))
                hour = dt.hour
                weekday = dt.weekday()   # 0=Mon … 6=Sun
            except Exception:
                hour, weekday = None, None

            videos.append({
                'id': v['id'],
                'title': vsnip.get('title'),
                'published_at': pub,
                'thumbnail': vsnip.get('thumbnails', {}).get('medium', {}).get('url'),
                'views': views,
                'likes': likes,
                'comments': comments,
                'engagement_rate': eng_rate,
                'hour': hour,
                'weekday': weekday,
            })

    # Sort by views descending
    videos.sort(key=lambda x: x['views'], reverse=True)

    # ── Best posting time heatmap ─────────────────────────────
    best_times = _compute_best_times(videos)

    # ── Engagement summary ────────────────────────────────────
    if videos:
        avg_eng = round(sum(v['engagement_rate'] for v in videos) / len(videos), 3)
        avg_views = int(sum(v['views'] for v in videos) / len(videos))
        top_video = videos[0]
    else:
        avg_eng, avg_views, top_video = 0, 0, None

    return {
        'platform': 'youtube',
        'channel': channel_info,
        'videos': videos[:50],
        'best_times': best_times,
        'summary': {
            'avg_engagement_rate': avg_eng,
            'avg_views_per_video': avg_views,
            'top_video': top_video,
            'videos_analyzed': len(videos),
        }
    }


def _compute_best_times(videos: list) -> dict:
    """
    Build a heatmap: {weekday: {hour: avg_views}}.
    Also returns ranked best day and best hour.
    """
    DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    day_hour_views = defaultdict(lambda: defaultdict(list))

    for v in videos:
        if v['hour'] is not None and v['weekday'] is not None:
            day_hour_views[v['weekday']][v['hour']].append(v['views'])

    # Average views per slot
    heatmap = {}
    for d in range(7):
        heatmap[DAYS[d]] = {}
        for h in range(24):
            vals = day_hour_views[d].get(h, [])
            heatmap[DAYS[d]][h] = int(sum(vals) / len(vals)) if vals else 0

    # Best day  
    day_totals = {d: sum(heatmap[d].values()) for d in DAYS}
    best_day = max(day_totals, key=day_totals.get) if day_totals else '—'

    # Best hour
    hour_totals = defaultdict(int)
    for d in DAYS:
        for h in range(24):
            hour_totals[h] += heatmap[d].get(h, 0)
    best_hour = max(hour_totals, key=hour_totals.get) if hour_totals else 0
    best_hour_label = f'{best_hour:02d}:00 – {(best_hour+1)%24:02d}:00'

    return {
        'heatmap': heatmap,
        'best_day': best_day,
        'best_hour': best_hour,
        'best_hour_label': best_hour_label,
        'day_order': DAYS,
    }


# ─────────────────────────────────────────────────────────────
#  Instagram Analytics
# ─────────────────────────────────────────────────────────────

def get_instagram_profile_stats(username: str, cookie_path: str = '') -> dict:
    """
    Extract public profile stats using instagrapi.
    Returns profile info, recent posts, best posting times.
    """
    try:
        from instagrapi import Client
    except ImportError:
        raise RuntimeError('instagrapi not installed. Run: pip install instagrapi')

    client = Client()

    # Load existing session cookies if available
    if cookie_path and os.path.isfile(cookie_path):
        try:
            # instagrapi uses its own session format; try loading settings
            client.load_settings(cookie_path)
        except Exception:
            pass

    user_info = client.user_info_by_username(username)
    followers = user_info.follower_count

    # Fetch up to 50 recent posts
    posts_raw = client.user_medias(user_info.pk, amount=50)

    posts = []
    for p in posts_raw:
        likes = p.like_count or 0
        comments = p.comment_count or 0
        eng = round((likes + comments) / max(followers, 1) * 100, 3)
        published = p.taken_at
        hour = published.hour if published else None
        weekday = published.weekday() if published else None

        posts.append({
            'id': str(p.id),
            'thumbnail': str(p.thumbnail_url or p.resources[0].thumbnail_url if p.resources else ''),
            'caption': (p.caption_text or '')[:120],
            'published_at': published.isoformat() if published else '',
            'likes': likes,
            'comments': comments,
            'engagement_rate': eng,
            'hour': hour,
            'weekday': weekday,
            'media_type': p.media_type,
        })

    posts.sort(key=lambda x: x['likes'], reverse=True)
    best_times = _compute_best_times(posts)

    avg_eng = round(sum(p['engagement_rate'] for p in posts) / len(posts), 3) if posts else 0
    avg_likes = int(sum(p['likes'] for p in posts) / len(posts)) if posts else 0

    return {
        'platform': 'instagram',
        'profile': {
            'username': username,
            'full_name': user_info.full_name,
            'biography': (user_info.biography or '')[:300],
            'followers': followers,
            'following': user_info.following_count,
            'post_count': user_info.media_count,
            'is_private': user_info.is_private,
            'profile_pic': str(user_info.profile_pic_url or ''),
        },
        'posts': posts[:50],
        'best_times': best_times,
        'summary': {
            'avg_engagement_rate': avg_eng,
            'avg_likes_per_post': avg_likes,
            'posts_analyzed': len(posts),
        }
    }
