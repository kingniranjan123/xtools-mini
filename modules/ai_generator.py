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
MODEL_CANDIDATES = [
    'google/gemini-2.5-pro',
    'google/gemini-2.0-flash-001',
    'openai/gpt-4o-mini',
    'meta-llama/llama-3.1-8b-instruct:free',
]
APP_NAME = 'Nikethan Reels Toolkit'


def _call_ai(api_key: str, system_prompt: str, user_prompt: str,
             temperature: float = 0.75) -> str:
    """Low-level call to OpenRouter chat completions endpoint."""
    if not HAS_REQUESTS:
        raise RuntimeError('requests library not installed.')
    if not api_key or len(api_key) < 10:
        raise ValueError('OpenRouter API key not configured. Go to Settings → API Keys.')

    last_error = 'Unknown OpenRouter error'
    for model_name in MODEL_CANDIDATES:
        resp = _req.post(
            f'{OPENROUTER_BASE}/chat/completions',
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
                'HTTP-Referer': 'http://localhost:5056',
                'X-Title': APP_NAME,
            },
            json={
                'model': model_name,
                'temperature': temperature,
                'max_tokens': 4096,
                'messages': [
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user',   'content': user_prompt},
                ],
            },
            timeout=60
        )

        if resp.status_code == 200:
            data    = resp.json()
            content = (data.get('choices') or [{}])[0].get('message', {}).get('content') or ''
            if content:
                return content.strip()
            # Empty content — try next model
            last_error = f'{model_name}: returned empty content'
            continue
        if resp.status_code in (401, 403):
            raise ValueError('OpenRouter API key is invalid or unauthorised.')
        if resp.status_code == 429:
            raise ValueError('OpenRouter rate limit hit. Wait a moment and try again.')

        # model not available for key/tier/etc.; try next candidate
        if resp.status_code in (404, 422):
            last_error = f'{model_name}: unavailable for this key/tier'
            continue

        last_error = f'{model_name}: HTTP {resp.status_code} {resp.text[:200]}'

    raise RuntimeError(last_error)



def _parse_json_block(text: str) -> dict:
    """
    Robustly extract a JSON object/array from an AI response.
    Handles: fenced blocks, truncated responses, plain JSON.
    Raises ValueError if nothing parses.
    """
    import re

    def try_parse(s):
        s = s.strip()
        if not s:
            raise ValueError('empty string')
        return json.loads(s)

    # Strategy 1: ```json ... ``` fences (standard Gemini output)
    if '```json' in text:
        after_fence = text.split('```json', 1)[1]
        # Prefer closed fence, but fall back to everything after the opening
        if '```' in after_fence:
            candidate = after_fence.rsplit('```', 1)[0].strip()
        else:
            # Truncated — no closing fence, try from start of JSON
            candidate = after_fence.strip()
        try:
            return try_parse(candidate)
        except (json.JSONDecodeError, ValueError):
            pass

    # Strategy 2: ``` ... ``` (no language tag)
    if text.count('```') >= 2:
        parts = text.split('```')
        for i in range(1, len(parts), 2):
            candidate = parts[i].strip()
            if candidate.startswith('{') or candidate.startswith('['):
                try:
                    return try_parse(candidate)
                except (json.JSONDecodeError, ValueError):
                    pass

    # Strategy 3: find the first { and try everything from there
    first_brace = text.find('{')
    if first_brace != -1:
        candidate = text[first_brace:]
        # Try the full slice
        try:
            return try_parse(candidate)
        except (json.JSONDecodeError, ValueError):
            pass
        # Try stripping after last }
        last_brace = candidate.rfind('}')
        if last_brace != -1:
            try:
                return try_parse(candidate[:last_brace + 1])
            except (json.JSONDecodeError, ValueError):
                pass

    # Strategy 4: direct parse of full text
    try:
        return try_parse(text)
    except (json.JSONDecodeError, ValueError):
        pass

    raise ValueError(
        f'Could not parse AI JSON response. '
        f'Response preview: {text[:300]!r}'
    )

# ─────────────────────────────────────────────────────────────
#  YouTube AI Content Generator
# ─────────────────────────────────────────────────────────────

YOUTUBE_SYSTEM = """You are a YouTube SEO expert and content creator.
Write in clear, natural, professional English.
Do NOT use Hindi, Tamil, Telugu, Malayalam, or transliterated local slang unless explicitly requested by the user.
STRICT LIMITS:
- Title: max 100 characters
- Description: max 300 words
- Tags: max 10 tags, each tag max 30 characters
Always respond with ONLY valid JSON. No markdown prose outside the JSON block."""

def generate_youtube_content(topic: str, api_key: str,
                              channel_niche: str = '',
                              is_short: bool = False,
                              language: str = 'english') -> dict:
    """
    Generate comprehensive YouTube content package from a one-line topic.
    language: 'english' = English-only | specific language name = generate in that language
    Returns dict with titles, description, tags with weightage, trending tags, hooks.
    """
    type_label    = 'YouTube Short (<=60 seconds, vertical)' if is_short else 'standard YouTube video'
    niche_context = f'Channel niche: {channel_niche}. ' if channel_niche else ''

    if language and language.lower() not in ('english', 'en'):
        lang_instruction = f'IMPORTANT: Generate ALL text content (titles, description, hooks, tags) entirely in {language}.'
    else:
        lang_instruction = (
            'LANGUAGE: Generate all content in clean global English only. '
            'Do not use Hindi/Tamil/Telugu/Malayalam words, transliterated slang, '
            'or local-language fillers.'
        )

    prompt = f"""
{niche_context}{lang_instruction}

Generate a YouTube content package for this video topic:
"{topic}"

Video type: {type_label}

STRICT RULES (follow exactly):
- Title: MAXIMUM 100 characters. Make it catchy and clear.
- Description: MAXIMUM 300 words in natural English. End with 3-4 relevant hashtags inline.
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
  "description": "300 words max. Natural English. Include a CTA at the end. #hashtag1 #hashtag2 #hashtag3",
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
    result = _parse_json_block(raw)  # raises ValueError on parse failure
    result['topic'] = topic
    result['is_short'] = is_short
    return result


# ─────────────────────────────────────────────────────────────
#  Instagram AI Content Generator
# ─────────────────────────────────────────────────────────────

INSTAGRAM_SYSTEM = """You are an Instagram content expert.
Write captions in natural, engaging, professional English.
Do NOT use Hindi, Tamil, Telugu, Malayalam, or transliterated local slang unless explicitly requested by the user.
STRICT LIMITS:
- Caption: max 300 words per caption
- Hashtags: max 10 hashtags total
Always respond with ONLY valid JSON. No markdown prose outside the JSON block."""


def generate_instagram_content(topic: str, api_key: str,
                                account_niche: str = '',
                                content_type: str = 'reel',
                                language: str = 'english') -> dict:
    """
    Generate comprehensive Instagram content package from a one-line topic.
    language: 'english' = English-only | specific language = generate in that language
    content_type: 'reel', 'post', or 'story'
    """
    niche_context = f'Account niche: {account_niche}. ' if account_niche else ''

    if language and language.lower() not in ('english', 'en'):
        lang_instruction = f'IMPORTANT: Write ALL captions and text entirely in {language}.'
    else:
        lang_instruction = (
            'LANGUAGE: Write all captions and text in clean global English only. '
            'Do not use Hindi/Tamil/Telugu/Malayalam words or transliterated slang.'
        )

    prompt = f"""
{niche_context}{lang_instruction}
Generate a complete Instagram content package for this topic:
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
    result = _parse_json_block(raw)  # raises ValueError on parse failure
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
    return _parse_json_block(raw)  # raises ValueError on parse failure
