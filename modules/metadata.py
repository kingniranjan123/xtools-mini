"""
Metadata extraction — parses yt-dlp info dict into structured fields.
"""
import re, json


def extract_metadata_from_info(info: dict) -> dict:
    """
    Given a yt-dlp raw info JSON dict, pull out:
      id, url, title, caption, tags (hashtags), mentions, duration, upload_date
    """
    description = info.get('description', '') or ''
    title       = info.get('title', '') or description[:80]

    tags     = _extract_hashtags(description)
    mentions = _extract_mentions(description)

    return {
        'id'         : info.get('id', ''),
        'url'        : info.get('webpage_url', info.get('url', '')),
        'title'      : title,
        'caption'    : description,
        'tags'       : tags,
        'mentions'   : mentions,
        'duration'   : int(info.get('duration') or 0),
        'upload_date': info.get('upload_date', ''),
        'view_count' : info.get('view_count'),
        'like_count' : info.get('like_count'),
    }


def extract_metadata_from_json(json_path: str) -> dict:
    """Load a yt-dlp .info.json sidecar and extract metadata."""
    with open(json_path, 'r', encoding='utf-8') as f:
        info = json.load(f)
    return extract_metadata_from_info(info)


def _extract_hashtags(text: str) -> list:
    """Return sorted unique list of hashtags (with #) from text."""
    tags = re.findall(r'#\w+', text, re.UNICODE)
    seen = set()
    unique = []
    for t in tags:
        tl = t.lower()
        if tl not in seen:
            seen.add(tl)
            unique.append(t)
    return unique


def _extract_mentions(text: str) -> list:
    """Return sorted unique list of @mentions from text."""
    mentions = re.findall(r'@[\w.]+', text, re.UNICODE)
    seen = set()
    unique = []
    for m in mentions:
        ml = m.lower()
        if ml not in seen:
            seen.add(ml)
            unique.append(m)
    return unique
