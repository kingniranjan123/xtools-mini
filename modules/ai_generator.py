"""
AI Content Generator — powered by OpenRouter / Gemini 2.5 Pro
Generates YouTube & Instagram content from a single-line user input.
"""
import json
import os

try:
    import requests as _req
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

OPENROUTER_BASE = 'https://openrouter.ai/api/v1'
MODEL = 'google/gemini-2.5-pro'
APP_NAME = 'Nikethan Reels Toolkit'


def _call_ai(api_key: str, system_prompt: str, user_prompt: str,
             temperature: float = 0.75) -> str:
    """Low-level call to OpenRouter chat completions endpoint."""
    if not HAS_REQUESTS:
        raise RuntimeError('requests library not installed.')
    if not api_key or len(api_key) < 10:
        raise ValueError('OpenRouter API key not configured. Go to Settings → API Keys.')

    resp = _req.post(
        f'{OPENROUTER_BASE}/chat/completions',
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
            'HTTP-Referer': 'http://localhost:5055',
            'X-Title': APP_NAME,
        },
        json={
            'model': MODEL,
            'temperature': temperature,
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user',   'content': user_prompt},
            ],
        },
        timeout=60
    )

    if resp.status_code == 401:
        raise ValueError('OpenRouter API key is invalid or unauthorised.')
    if resp.status_code == 429:
        raise ValueError('OpenRouter rate limit hit. Wait a moment and try again.')
    if resp.status_code != 200:
        raise RuntimeError(f'OpenRouter error {resp.status_code}: {resp.text[:300]}')

    data = resp.json()
    content = data['choices'][0]['message']['content']
    return content.strip()


def _parse_json_block(text: str) -> dict:
    """Extract the first JSON object or array from AI response."""
    # Strip markdown code fences if present
    clean = text
    if '```json' in clean:
        clean = clean.split('```json')[1].split('```')[0]
    elif '```' in clean:
        clean = clean.split('```')[1].split('```')[0]
    return json.loads(clean.strip())


# ─────────────────────────────────────────────────────────────
#  YouTube AI Content Generator
# ─────────────────────────────────────────────────────────────

YOUTUBE_SYSTEM = """You are an expert YouTube SEO strategist and viral content creator.
You specialise in creating high-ranking, click-worthy YouTube content that drives real engagement.
Always respond with ONLY valid JSON — no markdown prose outside the JSON block."""

def generate_youtube_content(topic: str, api_key: str,
                              channel_niche: str = '',
                              is_short: bool = False) -> dict:
    """
    Generate comprehensive YouTube content package from a one-line topic.
    Returns dict with titles, description, tags with weightage, trending tags, hooks.
    """
    type_label = 'YouTube Short (<=60 seconds, vertical)' if is_short else 'standard YouTube video'
    niche_context = f'Channel niche: {channel_niche}. ' if channel_niche else ''

    prompt = f"""
{niche_context}Generate a complete YouTube content package for this topic:
"{topic}"

Video type: {type_label}

Return ONLY valid JSON — no markdown, no extra prose:
{{
  "titles": [
    {{"text": "Title Option 1", "type": "curiosity_hook", "score": 95}},
    {{"text": "Title Option 2", "type": "how_to", "score": 88}},
    {{"text": "Title Option 3", "type": "listicle", "score": 82}}
  ],
  "description": "Full YouTube description (300-500 words). Include timestamps, CTA, social links, and #hashtags at end.",
  "tags": [
    {{"tag": "broad primary tag", "weight": 100, "type": "primary", "monthly_searches": "high"}},
    {{"tag": "second broad tag", "weight": 92, "type": "primary", "monthly_searches": "high"}},
    {{"tag": "niche specific tag", "weight": 85, "type": "secondary", "monthly_searches": "medium"}},
    {{"tag": "long tail phrase for this video", "weight": 75, "type": "long_tail", "monthly_searches": "medium"}},
    {{"tag": "related keyword 1", "weight": 68, "type": "secondary", "monthly_searches": "medium"}},
    {{"tag": "related keyword 2", "weight": 60, "type": "secondary", "monthly_searches": "medium"}},
    {{"tag": "community or brand tag", "weight": 52, "type": "niche", "monthly_searches": "low"}},
    {{"tag": "niche tag 1", "weight": 44, "type": "niche", "monthly_searches": "low"}},
    {{"tag": "niche tag 2", "weight": 36, "type": "niche", "monthly_searches": "low"}},
    {{"tag": "niche tag 3", "weight": 28, "type": "niche", "monthly_searches": "low"}},
    {{"tag": "hyper niche tag 1", "weight": 20, "type": "niche", "monthly_searches": "low"}},
    {{"tag": "hyper niche tag 2", "weight": 15, "type": "niche", "monthly_searches": "low"}},
    {{"tag": "long tail phrase 2", "weight": 45, "type": "long_tail", "monthly_searches": "low"}},
    {{"tag": "long tail phrase 3", "weight": 40, "type": "long_tail", "monthly_searches": "low"}},
    {{"tag": "broad search term variation", "weight": 80, "type": "primary", "monthly_searches": "high"}}
  ],
  "trending_now": {{
    "tag": "#ActualTrendingTagNow",
    "reason": "Why this tag is popular right now and how it connects to this video",
    "urgency": "Post within X days for maximum reach"
  }},
  "future_trending": {{
    "tag": "#PredictedFutureTrendTag",
    "reason": "Why this topic or keyword is predicted to grow significantly",
    "timeframe": "Expected to peak in X weeks/months — get in early"
  }},
  "category": "one of: Education, Entertainment, Gaming, Music, Film, Tech, Sports, News, People, Travel, Comedy, Science, HowTo",
  "thumbnail_headlines": [
    "PUNCHY HEADLINE 1 (max 5 words)",
    "PUNCHY HEADLINE 2 (max 5 words)",
    "PUNCHY HEADLINE 3 (max 5 words)"
  ],
  "hook_lines": [
    "Opening hook sentence 1 (first 15 seconds)",
    "Opening hook sentence 2 variant",
    "Opening hook sentence 3 variant"
  ],
  "end_screen_cta": "Compelling end-screen call to action (1 sentence)",
  "chapters": [
    {{"time": "00:00", "title": "Intro"}},
    {{"time": "01:30", "title": "Main topic chapter"}},
    {{"time": "05:00", "title": "Deep dive"}},
    {{"time": "09:30", "title": "Tips and tricks"}},
    {{"time": "12:00", "title": "Outro and CTA"}}
  ],
  "seo_keywords": ["primary search keyword", "secondary keyword", "3rd keyword"],
  "best_posting_time": "e.g. Tuesday or Thursday, 14:00-16:00 UTC",
  "content_tips": ["tip 1", "tip 2", "tip 3"]
}}

IMPORTANT for the tags array: MUST include exactly 15 tag objects sorted by weight descending. Weight 80-100 = primary broad tags with high search volume. Weight 50-79 = secondary medium competition tags. Weight 30-49 = long-tail phrases. Weight 1-29 = hyper-niche community tags. Set monthly_searches to 'high', 'medium', or 'low' accordingly.
"""
    raw = _call_ai(api_key, YOUTUBE_SYSTEM, prompt, temperature=0.8)
    try:
        result = _parse_json_block(raw)
    except Exception:
        # Return raw text if JSON parsing fails
        result = {'raw_response': raw, 'parse_error': True}
    result['topic'] = topic
    result['is_short'] = is_short
    return result


# ─────────────────────────────────────────────────────────────
#  Instagram AI Content Generator
# ─────────────────────────────────────────────────────────────

INSTAGRAM_SYSTEM = """You are an expert Instagram content strategist and growth hacker.
You specialise in viral Instagram Reels content, captions that stop the scroll, and
hashtag strategies that maximise reach for all follower sizes.
Always respond with ONLY valid JSON — no markdown prose outside the JSON block."""

def generate_instagram_content(topic: str, api_key: str,
                                account_niche: str = '',
                                content_type: str = 'reel') -> dict:
    """
    Generate comprehensive Instagram content package from a one-line topic.
    content_type: 'reel', 'post', or 'story'
    """
    niche_context = f'Account niche: {account_niche}. ' if account_niche else ''

    prompt = f"""
{niche_context}Generate a complete Instagram content package for this topic:
"{topic}"

Content type: Instagram {content_type.title()}

Return ONLY this JSON structure:
{{
  "captions": [
    {{
      "text": "Full caption with emojis, line breaks, and a strong hook in the first line. Mid-length (150-200 words). Includes CTA at the end.",
      "tone": "energetic",
      "hook": "First line of caption (the most critical — shown before 'more')"
    }},
    {{
      "text": "Alternative caption — different tone/angle",
      "tone": "informative",
      "hook": "Alternative first line hook"
    }},
    {{
      "text": "Short punchy version (under 80 words)",
      "tone": "casual",
      "hook": "Short hook"
    }}
  ],
  "hashtags": {{
    "mega": ["#hashtag (1M+ posts) — 5 tags"],
    "large": ["#hashtag (100K-1M posts) — 10 tags"],
    "medium": ["#hashtag (10K-100K posts) — 10 tags"],
    "niche": ["#hashtag (<10K posts) — 5 tags"],
    "full_set": "All 30 hashtags combined as a ready-to-paste string"
  }},
  "reel_hook": "The very first spoken/on-screen line for the video (max 10 words, creates curiosity)",
  "reel_script_outline": [
    "0-3s: Hook — what you say/show",
    "3-10s: Setup — expand the hook",
    "10-30s: Value delivery — main content",
    "30-55s: Climax / reveal",
    "55-60s: CTA"
  ],
  "story_sequence": [
    "Story slide 1 idea",
    "Story slide 2 idea",
    "Story slide 3 — poll/question to boost engagement",
    "Story slide 4 — link/CTA"
  ],
  "cta_options": [
    "Save this for later! 📌",
    "Tag a friend who needs to see this 👇",
    "Drop a 🔥 if you agree!"
  ],
  "best_posting_time": "e.g. Tue/Wed/Fri 11am–1pm or 7pm–9pm local time",
  "content_tips": ["Tip 1 for maximising reach", "Tip 2", "Tip 3"]
}}
"""
    raw = _call_ai(api_key, INSTAGRAM_SYSTEM, prompt, temperature=0.85)
    try:
        result = _parse_json_block(raw)
    except Exception:
        result = {'raw_response': raw, 'parse_error': True}
    result['topic'] = topic
    result['content_type'] = content_type
    return result


# ─────────────────────────────────────────────────────────────
#  Tag Expansion (VidIQ-style) for YouTube
# ─────────────────────────────────────────────────────────────

TAG_SYSTEM = """You are a YouTube SEO expert. Return ONLY valid JSON arrays, no prose."""

def expand_tags(seed_tags: list, api_key: str, niche: str = '') -> dict:
    """
    Given a list of seed tags, expand into a full SEO tag set with scoring.
    Returns scored, deduplicated tags grouped by type.
    """
    niche_ctx = f' in the {niche} niche' if niche else ''
    prompt = f"""
Given these seed tags{niche_ctx}: {json.dumps(seed_tags)}

Generate an expanded YouTube SEO tag set. Return ONLY this JSON:
{{
  "primary": ["tag1", "tag2", "tag3"],
  "secondary": ["tag4", "tag5", "tag6", "tag7", "tag8"],
  "long_tail": ["multi word phrase 1", "multi word phrase 2", "multi word phrase 3"],
  "trending_related": ["trending tag1", "trending tag2"],
  "all_tags": ["all 20 unique tags combined, deduplicated, ordered by estimated search volume desc"],
  "explanation": "1-2 sentence rationale for the strategy"
}}
"""
    raw = _call_ai(api_key, TAG_SYSTEM, prompt, temperature=0.5)
    try:
        return _parse_json_block(raw)
    except Exception:
        return {'raw_response': raw, 'parse_error': True}
