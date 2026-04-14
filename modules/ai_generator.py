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
            'max_tokens': 700,
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

YOUTUBE_SYSTEM = """You are a YouTube SEO expert and content creator writing for an Indian audience.
You write in a natural Indian-English style — friendly, conversational, energetic. Use common Indian expressions naturally (like 'yaar', 'bhai', 'ekdum', 'bilkul', 'full on', 'too good', 'mind-blowing stuff', 'must watch') but keep it professional.
STRICT LIMITS:
- Title: max 100 characters
- Description: max 300 words
- Tags: max 10 tags, each tag max 30 characters
Always respond with ONLY valid JSON. No markdown prose outside the JSON block."""

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
{niche_context}Generate a YouTube content package for this video topic (in Indian-English style):
"{topic}"

Video type: {type_label}

STRICT RULES (follow exactly):
- Title: MAXIMUM 100 characters. Make it catchy, use Indian hooks where relevant.
- Description: MAXIMUM 300 words. Write in friendly Indian-English (use expressions like 'yaar', 'ekdum solid', 'must watch', 'too good', 'bilkul free' where they fit naturally). End with 3-4 relevant hashtags inline.
- Tags: EXACTLY 10 tags. Each tag max 30 characters. Mix broad SEO + niche terms.
- trending_now: 1 currently trending tag related to topic with reason.
- future_trending: 1 predicted upcoming tag with reason + timeframe.

Return ONLY valid JSON, no extra text:
{{
  "titles": [
    {{"text": "Title under 100 chars", "type": "curiosity_hook", "score": 95}},
    {{"text": "Another title under 100 chars", "type": "how_to", "score": 88}},
    {{"text": "Third option under 100 chars", "type": "listicle", "score": 82}}
  ],
  "description": "300 words max. Friendly Indian-English. Include a CTA at the end. #hashtag1 #hashtag2 #hashtag3",
  "tags": [
    {{"tag": "primary tag", "weight": 100, "type": "primary", "monthly_searches": "high"}},
    {{"tag": "second tag", "weight": 90, "type": "primary", "monthly_searches": "high"}},
    {{"tag": "niche tag 1", "weight": 80, "type": "secondary", "monthly_searches": "medium"}},
    {{"tag": "niche tag 2", "weight": 70, "type": "secondary", "monthly_searches": "medium"}},
    {{"tag": "long tail phrase 1", "weight": 60, "type": "long_tail", "monthly_searches": "medium"}},
    {{"tag": "long tail phrase 2", "weight": 50, "type": "long_tail", "monthly_searches": "medium"}},
    {{"tag": "community tag 1", "weight": 40, "type": "niche", "monthly_searches": "low"}},
    {{"tag": "community tag 2", "weight": 30, "type": "niche", "monthly_searches": "low"}},
    {{"tag": "hyper niche 1", "weight": 20, "type": "niche", "monthly_searches": "low"}},
    {{"tag": "hyper niche 2", "weight": 10, "type": "niche", "monthly_searches": "low"}}
  ],
  "trending_now": {{
    "tag": "#TrendingTagNow",
    "reason": "Short reason why trending now (1-2 lines)",
    "urgency": "Post within X days for max reach"
  }},
  "future_trending": {{
    "tag": "#FutureTrendTag",
    "reason": "Why this will grow (1-2 lines)",
    "timeframe": "Expected to peak in X weeks/months"
  }},
  "category": "Education",
  "hook_lines": ["Hook line 1 (max 15 words)", "Hook line 2"],
  "best_posting_time": "Tue/Thu 6-9 PM IST"
}}
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

INSTAGRAM_SYSTEM = """You are an Instagram content expert writing for an Indian audience.
Write captions in natural Indian-English — fun, relatable, and engaging. Use expressions like 'yaar', 'ekdum viral', 'must follow', 'too good to miss', 'bilkul free hai', 'full paisa vasool' where they fit naturally.
STRICT LIMITS:
- Caption: max 300 words per caption
- Hashtags: max 10 hashtags total
Always respond with ONLY valid JSON. No markdown prose outside the JSON block."""


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
