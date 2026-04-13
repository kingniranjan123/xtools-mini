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


# ─────────────────────────────────────────────────────────────
#  Single Video Deep Analysis
# ─────────────────────────────────────────────────────────────

def get_youtube_video_deep_analysis(video_id_or_url: str, api_key: str,
                                     channel_avg: dict = None) -> dict:
    """
    Deep-dive analytics for a single YouTube video.
    Compares the video's performance against channel averages if provided.
    video_id_or_url: full URL or bare video ID (11-char).
    """
    if not HAS_REQUESTS:
        raise RuntimeError('requests library not installed.')

    # Extract video ID
    vid_match = re.search(r'(?:v=|youtu\.be/|shorts/)([\w-]{11})', video_id_or_url)
    video_id = vid_match.group(1) if vid_match else video_id_or_url.strip()
    if len(video_id) != 11:
        raise ValueError(f'Invalid video ID: {video_id_or_url}')

    # Fetch full video data
    data = _yt_get('videos', {
        'part': 'snippet,statistics,contentDetails,topicDetails',
        'id': video_id
    }, api_key)

    items = data.get('items', [])
    if not items:
        raise ValueError(f'Video not found: {video_id}')

    v = items[0]
    vs     = v.get('statistics', {})
    vsnip  = v.get('snippet', {})
    vcd    = v.get('contentDetails', {})
    vtopic = v.get('topicDetails', {})

    views    = int(vs.get('viewCount', 0))
    likes    = int(vs.get('likeCount', 0))
    comments = int(vs.get('commentCount', 0))
    duration_secs = _parse_duration(vcd.get('duration', ''))
    tags     = vsnip.get('tags', [])
    title    = vsnip.get('title', '')
    desc     = vsnip.get('description', '')

    is_short = duration_secs > 0 and duration_secs <= 180  # <= 3 mins
    eng_rate = round((likes + comments) / max(views, 1) * 100, 3)
    like_ratio    = round(likes    / max(views, 1) * 100, 3)
    comment_ratio = round(comments / max(views, 1) * 100, 3)

    pub = vsnip.get('publishedAt', '')
    try:
        pub_dt = datetime.fromisoformat(pub.replace('Z', '+00:00'))
        days_since = max((datetime.now(timezone.utc) - pub_dt).days, 1)
        view_velocity = round(views / days_since)
        publish_hour = pub_dt.hour
        publish_day  = DAYS[pub_dt.weekday()]
    except Exception:
        days_since = 1
        view_velocity = views
        publish_hour = None
        publish_day  = '—'

    # Thumbnails (all resolutions)
    thumbs = vsnip.get('thumbnails', {})
    thumbnails = {
        'default':  thumbs.get('default',  {}).get('url'),
        'medium':   thumbs.get('medium',   {}).get('url'),
        'high':     thumbs.get('high',     {}).get('url'),
        'standard': thumbs.get('standard', {}).get('url'),
        'maxres':   thumbs.get('maxres',   {}).get('url'),
    }

    # Topics
    topic_categories = [
        t.split('/')[-1] for t in vtopic.get('topicCategories', [])
    ]

    # vs channel averages
    vs_channel = {}
    if channel_avg:
        ca = channel_avg
        vs_channel = {
            'views_vs_avg':    _pct_diff(views,    ca.get('avg_views', 0)),
            'likes_vs_avg':    _pct_diff(likes,    ca.get('avg_likes', 0)),
            'comments_vs_avg': _pct_diff(comments, ca.get('avg_comments', 0)),
            'eng_vs_avg':      _pct_diff(eng_rate, ca.get('avg_engagement_rate', 0)),
            'velocity_vs_avg': _pct_diff(view_velocity, ca.get('avg_view_velocity', 0)),
        }

    # Description keyword density
    desc_words = re.findall(r'\b\w{4,}\b', desc.lower())
    from collections import Counter
    top_desc_words = Counter(desc_words).most_common(10)

    return {
        'video_id':        video_id,
        'url':             f'https://youtube.com/watch?v={video_id}',
        'title':           title,
        'title_length':    len(title),
        'description':     desc[:500],
        'description_length': len(desc),
        'published_at':    pub,
        'publish_hour_utc': publish_hour,
        'publish_day':     publish_day,
        'days_live':       days_since,
        'thumbnails':      thumbnails,
        'channel_id':      vsnip.get('channelId'),
        'channel_title':   vsnip.get('channelTitle'),
        'category_id':     vsnip.get('categoryId'),
        'is_short':        is_short,
        'duration_secs':   duration_secs,
        'duration_fmt':    _fmt_duration(duration_secs),
        'tags':            tags,
        'tag_count':       len(tags),
        'topic_categories': topic_categories,
        'views':           views,
        'likes':           likes,
        'comments':        comments,
        'engagement_rate': eng_rate,
        'like_ratio':      like_ratio,
        'comment_ratio':   comment_ratio,
        'view_velocity':   view_velocity,
        'top_desc_keywords': [{'word': w, 'count': c} for w, c in top_desc_words],
        'vs_channel':      vs_channel,
        'performance_grade': _grade_video(eng_rate, view_velocity),
    }


def _pct_diff(val, avg):
    """Return % difference vs average, signed."""
    if not avg:
        return None
    return round((val - avg) / avg * 100, 1)


def _grade_video(eng_rate, velocity):
    """Return a simple A/B/C/D performance grade."""
    score = 0
    if eng_rate >= 10: score += 3
    elif eng_rate >= 5: score += 2
    elif eng_rate >= 1: score += 1

    if velocity >= 10000: score += 3
    elif velocity >= 1000: score += 2
    elif velocity >= 100: score += 1

    if score >= 5: return 'A+'
    if score >= 4: return 'A'
    if score >= 3: return 'B'
    if score >= 2: return 'C'
    return 'D'


# ─────────────────────────────────────────────────────────────
#  YouTube Trending (Day / Week / Month, Shorts vs Long)
# ─────────────────────────────────────────────────────────────

YT_CATEGORY_NAMES = {
    '1': 'Film & Animation', '2': 'Autos & Vehicles', '10': 'Music',
    '15': 'Pets & Animals', '17': 'Sports', '19': 'Travel & Events',
    '20': 'Gaming', '22': 'People & Blogs', '23': 'Comedy',
    '24': 'Entertainment', '25': 'News & Politics', '26': 'How-to & Style',
    '27': 'Education', '28': 'Science & Technology', '29': 'Non-profits & Activism',
}

TRENDING_WINDOWS = {
    'day':   1,
    'week':  7,
    'month': 30,
}


def get_youtube_trending(api_key: str, region_code: str = 'US',
                         category_id: str = '0',
                         window: str = 'day',
                         max_results: int = 50) -> dict:
    """
    Fetch YouTube trending videos via chart=mostPopular.
    Splits results into Shorts (≤3 min) and Long-form (>3 min).
    Applies a view-velocity filter to approximate day/week/month freshness.
    """
    if not HAS_REQUESTS:
        raise RuntimeError('requests library not installed.')

    params = {
        'part':       'snippet,statistics,contentDetails',
        'chart':      'mostPopular',
        'regionCode': region_code,
        'maxResults': 50,
    }
    if category_id and category_id != '0':
        params['videoCategoryId'] = category_id

    data = _yt_get('videos', params, api_key)
    raw_items = data.get('items', [])

    cutoff_days = TRENDING_WINDOWS.get(window, 1)
    cutoff_dt   = datetime.now(timezone.utc) - timedelta(days=cutoff_days)

    shorts = []
    long_form = []

    for v in raw_items:
        vs    = v.get('statistics', {})
        vsnip = v.get('snippet', {})
        vcd   = v.get('contentDetails', {})

        views    = int(vs.get('viewCount', 0))
        likes    = int(vs.get('likeCount', 0))
        comments = int(vs.get('commentCount', 0))
        duration_secs = _parse_duration(vcd.get('duration', ''))
        title = vsnip.get('title', '')
        pub   = vsnip.get('publishedAt', '')
        tags  = vsnip.get('tags', [])

        try:
            pub_dt = datetime.fromisoformat(pub.replace('Z', '+00:00'))
            days_since = max((datetime.now(timezone.utc) - pub_dt).days, 1)
            view_velocity = round(views / days_since)
        except Exception:
            pub_dt = None
            days_since = 1
            view_velocity = views

        eng_rate = round((likes + comments) / max(views, 1) * 100, 3)
        is_short = duration_secs > 0 and duration_secs <= 180

        entry = {
            'id':              v['id'],
            'url':             f'https://youtube.com/watch?v={v["id"]}',
            'title':           title,
            'thumbnail':       vsnip.get('thumbnails', {}).get('medium', {}).get('url'),
            'channel_title':   vsnip.get('channelTitle'),
            'channel_id':      vsnip.get('channelId'),
            'published_at':    pub,
            'duration_secs':   duration_secs,
            'duration_fmt':    _fmt_duration(duration_secs),
            'is_short':        is_short,
            'views':           views,
            'likes':           likes,
            'comments':        comments,
            'engagement_rate': eng_rate,
            'view_velocity':   view_velocity,
            'days_live':       days_since,
            'tag_count':       len(tags),
            'category_id':     vsnip.get('categoryId', '—'),
            'category_name':   YT_CATEGORY_NAMES.get(vsnip.get('categoryId', ''), 'Other'),
        }

        # For week/month windows: filter out videos older than window
        if pub_dt and window in ('week', 'month'):
            if pub_dt < cutoff_dt:
                # Still include but mark as outside window
                entry['outside_window'] = True
            else:
                entry['outside_window'] = False
        else:
            entry['outside_window'] = False

        if is_short:
            shorts.append(entry)
        else:
            long_form.append(entry)

    # Sort each by view velocity (freshness-weighted)
    shorts.sort(key=lambda x: x['view_velocity'], reverse=True)
    long_form.sort(key=lambda x: x['view_velocity'], reverse=True)

    return {
        'window':      window,
        'region_code': region_code,
        'category_id': category_id,
        'category_name': YT_CATEGORY_NAMES.get(category_id, 'All Categories') if category_id != '0' else 'All Categories',
        'shorts':      shorts,
        'long_form':   long_form,
        'total':       len(shorts) + len(long_form),
        'fetched_at':  datetime.utcnow().isoformat(),
    }

