"""
Channel Analytics Module — VidIQ-parity
Extracts 31+ metrics from YouTube Data API v3 (any public channel).
"""
import os
import re
import json
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from math import ceil

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

YT_BASE = 'https://www.googleapis.com/youtube/v3'
DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
          'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']


# ─────────────────────────────────────────────────────────────
#  Internal Helpers
# ─────────────────────────────────────────────────────────────

def _yt_get(endpoint, params, api_key):
    params['key'] = api_key
    resp = requests.get(f'{YT_BASE}/{endpoint}', params=params, timeout=12)
    if resp.status_code == 403:
        data = resp.json()
        reason = data.get('error', {}).get('errors', [{}])[0].get('reason', '')
        if reason == 'quotaExceeded':
            raise ValueError('YouTube API daily quota exceeded (10,000 units). Try again tomorrow.')
        raise ValueError('YouTube API key is invalid or access denied.')
    if resp.status_code != 200:
        raise RuntimeError(f'YouTube API error {resp.status_code}: {resp.text[:200]}')
    return resp.json()


def _resolve_channel_id(input_str, api_key):
    ucid_match = re.search(r'UC[\w-]{22}', input_str)
    if ucid_match:
        return ucid_match.group(0)

    handle_match = re.search(r'@([\w.]+)', input_str)
    handle = handle_match.group(1) if handle_match else input_str.lstrip('@').strip()

    data = _yt_get('channels', {'part': 'id', 'forHandle': f'@{handle}', 'maxResults': 1}, api_key)
    items = data.get('items', [])
    if not items:
        # Fallback: search
        search = _yt_get('search', {'part': 'snippet', 'q': handle, 'type': 'channel', 'maxResults': 1}, api_key)
        s_items = search.get('items', [])
        if not s_items:
            raise ValueError(f'Channel not found: {input_str}')
        return s_items[0]['snippet']['channelId']
    return items[0]['id']


def _parse_duration(iso: str) -> int:
    """Parse ISO 8601 duration (e.g. PT4M13S) to seconds."""
    m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', iso or '')
    if not m:
        return 0
    h, mn, s = (int(x or 0) for x in m.groups())
    return h * 3600 + mn * 60 + s


def _fmt_duration(secs: int) -> str:
    if secs <= 0: return '0:00'
    h = secs // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    if h > 0:
        return f'{h}:{m:02d}:{s:02d}'
    return f'{m}:{s:02d}'


def _compute_best_times(items: list) -> dict:
    """Build day×hour heatmap and monthly pattern from a list of dicts with hour/weekday/month keys."""
    day_hour = defaultdict(lambda: defaultdict(list))
    month_counts = defaultdict(int)

    for v in items:
        wday = v.get('weekday')
        hour = v.get('hour')
        month = v.get('month')
        views = v.get('views', v.get('likes', 0))

        if wday is not None and hour is not None:
            day_hour[wday][hour].append(views)
        if month is not None:
            month_counts[month] += 1

    heatmap = {}
    for d in range(7):
        heatmap[DAYS[d]] = {}
        for h in range(24):
            vals = day_hour[d].get(h, [])
            heatmap[DAYS[d]][h] = int(sum(vals) / len(vals)) if vals else 0

    # Best day
    day_totals = {DAYS[d]: sum(heatmap[DAYS[d]].values()) for d in range(7)}
    best_day = max(day_totals, key=day_totals.get) if any(day_totals.values()) else '—'

    # Best hour
    hour_totals = defaultdict(int)
    for d in range(7):
        for h in range(24):
            hour_totals[h] += heatmap[DAYS[d]].get(h, 0)
    best_hour = max(hour_totals, key=hour_totals.get) if any(hour_totals.values()) else 0
    best_hour_label = f'{best_hour:02d}:00 – {(best_hour + 1) % 24:02d}:00 UTC'

    monthly = {MONTHS[m]: month_counts.get(m, 0) for m in range(12)}

    return {
        'heatmap': heatmap,
        'day_order': DAYS,
        'best_day': best_day,
        'best_hour': best_hour,
        'best_hour_label': best_hour_label,
        'monthly_pattern': monthly,
    }


# ─────────────────────────────────────────────────────────────
#  Main YouTube Analytics Function
# ─────────────────────────────────────────────────────────────

def get_youtube_channel_stats(channel_input: str, api_key: str) -> dict:
    """Full VidIQ-parity analytics for any public YouTube channel."""
    if not HAS_REQUESTS:
        raise RuntimeError('requests library not installed.')
    if not api_key or len(api_key) < 10:
        raise ValueError('No YouTube API key configured. Go to Settings → API Keys.')

    channel_id = _resolve_channel_id(channel_input, api_key)

    # ── Channel Metadata ──────────────────────────────────────
    ch_data = _yt_get('channels', {
        'part': 'snippet,statistics,contentDetails,brandingSettings',
        'id': channel_id
    }, api_key)

    if not ch_data.get('items'):
        raise ValueError(f'Channel not found: {channel_input}')

    ch = ch_data['items'][0]
    snippet = ch['snippet']
    stats = ch['statistics']
    uploads_playlist = ch['contentDetails']['relatedPlaylists']['uploads']

    joined_str = snippet.get('publishedAt', '')
    try:
        joined_dt = datetime.fromisoformat(joined_str.replace('Z', '+00:00'))
        channel_age_days = (datetime.now(timezone.utc) - joined_dt).days
    except Exception:
        channel_age_days = 0
        joined_dt = None

    subscribers = int(stats.get('subscriberCount', 0))
    total_views = int(stats.get('viewCount', 0))
    video_count = int(stats.get('videoCount', 0))

    channel_info = {
        'id': channel_id,
        'title': snippet.get('title'),
        'description': snippet.get('description', '')[:300],
        'thumbnail': snippet.get('thumbnails', {}).get('high', {}).get('url'),
        'published_at': joined_str,
        'channel_age_days': channel_age_days,
        'country': snippet.get('country', '—'),
        'subscribers': subscribers,
        'total_views': total_views,
        'video_count': video_count,
        'hidden_subscribers': stats.get('hiddenSubscriberCount', False),
        # Derived
        'views_per_subscriber': round(total_views / max(subscribers, 1), 1),
        'avg_views_per_day': round(total_views / max(channel_age_days, 1)),
        'uploads_per_week': round(video_count / max(channel_age_days / 7, 1), 2),
    }

    # ── Fetch up to 50 video IDs ──────────────────────────────
    video_ids = []
    page_token = None
    while len(video_ids) < 50:
        params = {'part': 'contentDetails', 'playlistId': uploads_playlist, 'maxResults': 50}
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

    # ── Fetch video details ───────────────────────────────────
    videos = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        vid_data = _yt_get('videos', {
            'part': 'snippet,statistics,contentDetails',
            'id': ','.join(batch)
        }, api_key)

        for v in vid_data.get('items', []):
            vs = v.get('statistics', {})
            vsnip = v.get('snippet', {})
            vcd = v.get('contentDetails', {})

            views    = int(vs.get('viewCount', 0))
            likes    = int(vs.get('likeCount', 0))
            comments = int(vs.get('commentCount', 0))
            duration_secs = _parse_duration(vcd.get('duration', ''))
            tags = vsnip.get('tags', [])
            title = vsnip.get('title', '')
            desc = vsnip.get('description', '')

            eng_rate = round((likes + comments) / max(views, 1) * 100, 3)
            like_ratio = round(likes / max(views, 1) * 100, 3)
            comment_ratio = round(comments / max(views, 1) * 100, 3)

            pub = vsnip.get('publishedAt', '')
            try:
                dt = datetime.fromisoformat(pub.replace('Z', '+00:00'))
                hour = dt.hour
                weekday = dt.weekday()
                month = dt.month - 1  # 0-indexed
                # View velocity: views per day since publish
                days_since = max((datetime.now(timezone.utc) - dt).days, 1)
                view_velocity = round(views / days_since)
            except Exception:
                hour = weekday = month = None
                view_velocity = 0

            videos.append({
                'id': v['id'],
                'title': title,
                'title_length': len(title),
                'description_length': len(desc),
                'published_at': pub,
                'thumbnail': vsnip.get('thumbnails', {}).get('medium', {}).get('url'),
                'views': views,
                'likes': likes,
                'comments': comments,
                'engagement_rate': eng_rate,
                'like_ratio': like_ratio,
                'comment_ratio': comment_ratio,
                'duration_secs': duration_secs,
                'duration_fmt': _fmt_duration(duration_secs),
                'tag_count': len(tags),
                'tags': tags[:10],
                'view_velocity': view_velocity,
                'hour': hour,
                'weekday': weekday,
                'month': month,
            })

    videos.sort(key=lambda x: x['views'], reverse=True)

    # ── Best posting time ─────────────────────────────────────
    best_times = _compute_best_times(videos)

    # ── Content Analysis ──────────────────────────────────────
    n = len(videos)
    if n > 0:
        avg_views          = int(sum(v['views'] for v in videos) / n)
        avg_likes          = int(sum(v['likes'] for v in videos) / n)
        avg_comments       = int(sum(v['comments'] for v in videos) / n)
        avg_eng            = round(sum(v['engagement_rate'] for v in videos) / n, 3)
        avg_duration       = int(sum(v['duration_secs'] for v in videos) / n)
        avg_title_len      = round(sum(v['title_length'] for v in videos) / n, 1)
        avg_desc_len       = round(sum(v['description_length'] for v in videos) / n, 1)
        avg_tag_count      = round(sum(v['tag_count'] for v in videos) / n, 1)
        avg_like_ratio     = round(sum(v['like_ratio'] for v in videos) / n, 3)
        avg_comment_ratio  = round(sum(v['comment_ratio'] for v in videos) / n, 3)
        avg_velocity       = int(sum(v['view_velocity'] for v in videos) / n)

        # Upload cadence: median gap between uploads
        pub_dates = sorted(
            [v['published_at'] for v in videos if v['published_at']],
            reverse=True
        )
        if len(pub_dates) >= 2:
            gaps = []
            for i in range(len(pub_dates) - 1):
                try:
                    d1 = datetime.fromisoformat(pub_dates[i].replace('Z', '+00:00'))
                    d2 = datetime.fromisoformat(pub_dates[i+1].replace('Z', '+00:00'))
                    gaps.append(abs((d1 - d2).days))
                except Exception:
                    pass
            avg_upload_gap_days = round(sum(gaps) / len(gaps), 1) if gaps else None
        else:
            avg_upload_gap_days = None

        # Top performers
        top_by_views    = videos[:5]
        top_by_eng      = sorted(videos, key=lambda x: x['engagement_rate'], reverse=True)[:5]
        top_by_velocity = sorted(videos, key=lambda x: x['view_velocity'], reverse=True)[:5]
        # Recent breakout: top 10 recent videos sorted by view velocity
        breakouts = sorted(videos[:15], key=lambda x: x['view_velocity'], reverse=True)[:3]
    else:
        avg_views = avg_likes = avg_comments = avg_eng = avg_duration = 0
        avg_title_len = avg_desc_len = avg_tag_count = avg_like_ratio = 0
        avg_comment_ratio = avg_velocity = avg_upload_gap_days = None
        top_by_views = top_by_eng = top_by_velocity = breakouts = []

    return {
        'platform': 'youtube',
        'channel': channel_info,
        'videos': videos[:50],
        'best_times': best_times,
        'content_analysis': {
            'avg_views': avg_views,
            'avg_likes': avg_likes,
            'avg_comments': avg_comments,
            'avg_engagement_rate': avg_eng,
            'avg_duration_secs': avg_duration,
            'avg_duration_fmt': _fmt_duration(avg_duration),
            'avg_title_length': avg_title_len,
            'avg_description_length': avg_desc_len,
            'avg_tag_count': avg_tag_count,
            'avg_like_ratio': avg_like_ratio,
            'avg_comment_ratio': avg_comment_ratio,
            'avg_view_velocity': avg_velocity,
            'avg_upload_gap_days': avg_upload_gap_days,
        },
        'top_performers': {
            'by_views':    top_by_views,
            'by_engagement': top_by_eng,
            'by_velocity': top_by_velocity,
            'breakouts':   breakouts,
        },
        'videos_analyzed': n,
    }


# ─────────────────────────────────────────────────────────────
#  Multi-channel bulk analysis (returns comparison table rows)
# ─────────────────────────────────────────────────────────────

def get_multi_channel_comparison(channel_inputs: list, api_key: str) -> list:
    """
    Analyse multiple channels and return a list of lightweight summary dicts.
    Used for the side-by-side comparison table.
    """
    results = []
    for inp in channel_inputs:
        inp = inp.strip()
        if not inp:
            continue
        try:
            data = get_youtube_channel_stats(inp, api_key)
            ch = data['channel']
            ca = data['content_analysis']
            bt = data['best_times']
            results.append({
                'input': inp,
                'channel_id': ch['id'],
                'title': ch['title'],
                'thumbnail': ch['thumbnail'],
                'subscribers': ch['subscribers'],
                'total_views': ch['total_views'],
                'video_count': ch['video_count'],
                'channel_age_days': ch['channel_age_days'],
                'uploads_per_week': ch['uploads_per_week'],
                'views_per_subscriber': ch['views_per_subscriber'],
                'avg_views': ca['avg_views'],
                'avg_engagement_rate': ca['avg_engagement_rate'],
                'avg_duration_fmt': ca['avg_duration_fmt'],
                'avg_title_length': ca['avg_title_length'],
                'avg_tag_count': ca['avg_tag_count'],
                'avg_view_velocity': ca['avg_view_velocity'],
                'avg_upload_gap_days': ca['avg_upload_gap_days'],
                'best_day': bt['best_day'],
                'best_hour_label': bt['best_hour_label'],
                'error': None,
                'full_data': data,
            })
        except Exception as e:
            results.append({'input': inp, 'error': str(e), 'title': inp})
    return results


# ─────────────────────────────────────────────────────────────
#  Instagram Analytics
# ─────────────────────────────────────────────────────────────

def get_instagram_profile_stats(username: str, cookie_path: str = '') -> dict:
    try:
        from instagrapi import Client
    except ImportError:
        raise RuntimeError('instagrapi not installed.')

    client = Client()
    if cookie_path and os.path.isfile(cookie_path):
        try:
            client.load_settings(cookie_path)
        except Exception:
            pass

    user_info = client.user_info_by_username(username)
    followers = user_info.follower_count
    posts_raw = client.user_medias(user_info.pk, amount=50)

    posts = []
    for p in posts_raw:
        likes = p.like_count or 0
        comments = p.comment_count or 0
        eng = round((likes + comments) / max(followers, 1) * 100, 3)
        published = p.taken_at
        hour    = published.hour if published else None
        weekday = published.weekday() if published else None
        month   = (published.month - 1) if published else None

        posts.append({
            'id': str(p.id),
            'thumbnail': str(p.thumbnail_url or ''),
            'caption': (p.caption_text or '')[:120],
            'published_at': published.isoformat() if published else '',
            'likes': likes,
            'comments': comments,
            'engagement_rate': eng,
            'hour': hour,
            'weekday': weekday,
            'month': month,
        })

    posts.sort(key=lambda x: x['likes'], reverse=True)
    best_times = _compute_best_times(posts)

    n = len(posts)
    avg_eng   = round(sum(p['engagement_rate'] for p in posts) / n, 3) if n else 0
    avg_likes = int(sum(p['likes'] for p in posts) / n) if n else 0

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
            'posts_analyzed': n,
        }
    }
