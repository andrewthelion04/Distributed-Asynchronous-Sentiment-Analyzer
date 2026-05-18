import json
import logging
import re
from collections import Counter

import httpx
import redis
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from .ingestor import manager

logger = logging.getLogger(__name__)

_TWITCH_URL_RE = re.compile(
    r'^(?:https?://)?(?:www\.)?twitch\.tv/([a-zA-Z0-9_]{1,25})(?:[/?].*)?$'
)
_PLAIN_NAME_RE = re.compile(r'^[a-zA-Z0-9_]{1,25}$')

_redis_client = redis.Redis(host=settings.REDIS_HOST, port=6379, decode_responses=True)

_STOP_WORDS = {
    'the', 'a', 'an', 'is', 'it', 'in', 'on', 'at', 'to', 'for', 'of',
    'and', 'or', 'but', 'not', 'with', 'this', 'that', 'are', 'was',
    'i', 'u', 'r', 'ur', 'im', 'so', 'just', 'me', 'my', 'you', 'your',
}


def _extract_channel(raw: str) -> str | None:
    raw = raw.strip().rstrip('/')
    m = _TWITCH_URL_RE.match(raw)
    if m:
        return m.group(1).lower()
    if _PLAIN_NAME_RE.match(raw):
        return raw.lower()
    return None


def index(request):
    return render(request, 'analyzer/index.html', {
        'active_channel': manager.active_channel,
    })


def _fetch_banner_url(channel: str) -> str | None:
    """Best-effort fetch of the streamer's offline image / banner via decapi.me.

    Tries the offline image first (typically a wide, high-res scene shot, ideal
    as a blurred background); falls back to the profile banner. Both endpoints
    return a plain-text URL or a "No ... set." message when absent.
    """
    endpoints = (
        f'https://decapi.me/twitch/offline_image/{channel}',
        f'https://decapi.me/twitch/banner/{channel}',
    )
    for ep in endpoints:
        try:
            r = httpx.get(ep, timeout=2.5)
            if r.status_code != 200:
                continue
            url = r.text.strip()
            if url.startswith('http'):
                return url
        except httpx.RequestError as exc:
            logger.warning('Banner fetch failed for %s: %s', ep, exc)
            continue
    return None


@require_http_methods(['POST'])
def start_stream(request):
    raw = request.POST.get('url', '').strip()
    channel = _extract_channel(raw)
    if not channel:
        return JsonResponse({'error': 'URL sau username Twitch invalid.'}, status=400)

    manager.start(
        channel=channel,
        rabbitmq_host=settings.RABBITMQ_HOST,
        rabbitmq_user=settings.RABBITMQ_USER,
        rabbitmq_pass=settings.RABBITMQ_PASS,
    )
    return JsonResponse({
        'channel': channel,
        'banner': _fetch_banner_url(channel),
    })


@require_http_methods(['POST'])
def stop_stream(request):
    manager.stop()
    return JsonResponse({'stopped': True})


def stream_status(request):
    return JsonResponse({'channel': manager.active_channel})


# ── LLM endpoints ─────────────────────────────────────────────────────────────


def _get_buffer_messages():
    """Returns (messages, error_response). `messages` is None when there's an error."""
    channel = manager.active_channel
    if not channel:
        return None, JsonResponse({'error': 'Niciun canal activ.'}, status=400)

    raw_messages = _redis_client.lrange(f'chat_buffer:{channel}', 0, 99)
    if not raw_messages:
        return None, JsonResponse({'error': 'Nu există mesaje în buffer încă.'}, status=400)

    messages = []
    for m in raw_messages:
        try:
            messages.append(json.loads(m))
        except json.JSONDecodeError:
            pass
    return messages, None


def _llm_call(prompt: str, messages: list, api_key: str, max_tokens: int = 200) -> str:
    """Call Groq with `prompt` + the joined chat messages. Raises on failure."""
    from groq import Groq
    chat_text = '\n'.join(
        f"{m.get('username', '?')}: {m.get('text', '')}"
        for m in messages
    )
    client = Groq(api_key=api_key)
    response = client.chat.completions.create(
        model='llama-3.1-8b-instant',
        max_tokens=max_tokens,
        messages=[{
            'role': 'user',
            'content': f'{prompt}\n\nMessages:\n{chat_text}',
        }],
    )
    return response.choices[0].message.content.strip()


@require_http_methods(['POST'])
def generate_summary(request):
    messages, err = _get_buffer_messages()
    if err:
        return err

    prompt = (
        'Summarize in 1-2 sentences what the main topic of discussion is '
        'in this Twitch chat. Ignore spam and meaningless emotes.'
    )
    groq_key = getattr(settings, 'GROQ_API_KEY', '')

    if groq_key:
        try:
            summary = _llm_call(prompt, messages, groq_key, max_tokens=150)
        except Exception as exc:
            summary = f'[Eroare Groq: {exc}] ' + _local_summary(messages)
    else:
        summary = _local_summary(messages)

    return JsonResponse({'summary': summary, 'count': len(messages)})


@require_http_methods(['POST'])
def extract_questions(request):
    messages, err = _get_buffer_messages()
    if err:
        return err

    prompt = (
        'Analyze these Twitch chat messages. Extract and list ONLY the clear '
        'and relevant questions addressed to the streamer. Ignore rhetorical '
        'questions, spam, and emote chains. Return one question per line, '
        'without numbering or bullets. If there are no clear questions, '
        'respond with the single word: NONE.'
    )
    groq_key = getattr(settings, 'GROQ_API_KEY', '')

    questions: list[str] = []
    note = ''
    if groq_key:
        try:
            raw = _llm_call(prompt, messages, groq_key, max_tokens=400)
            if raw.strip().upper() != 'NONE':
                questions = [
                    line.strip().lstrip('-•*0123456789. )').strip()
                    for line in raw.splitlines()
                    if line.strip()
                ]
                questions = [q for q in questions if q and len(q) > 4]
        except Exception as exc:
            note = f'[Eroare Groq: {exc}] '
            questions = _local_questions(messages)
    else:
        questions = _local_questions(messages)
        note = '(Heuristic local — adaugă GROQ_API_KEY pentru extracţie AI.)'

    return JsonResponse({
        'questions': questions,
        'count': len(messages),
        'note': note,
    })


@require_http_methods(['POST'])
def vibe_check(request):
    messages, err = _get_buffer_messages()
    if err:
        return err

    prompt = (
        'Analyze the mood (vibe) of this Twitch chat. Respond with a single '
        'short phrase (maximum 3 words) describing the general vibe. '
        'Examples: "Hype", "Trolling", "Confusion", "Chill vibes", "Toxic", '
        '"Excitement", "Boredom". Respond ONLY with the vibe phrase, no '
        'explanation, no punctuation, no quotes.'
    )
    groq_key = getattr(settings, 'GROQ_API_KEY', '')

    note = ''
    if groq_key:
        try:
            vibe = _llm_call(prompt, messages, groq_key, max_tokens=20)
            vibe = vibe.strip().strip('"\'.,!? ').split('\n')[0][:60] or _local_vibe(messages)
        except Exception as exc:
            note = f'[Eroare Groq: {exc}] '
            vibe = _local_vibe(messages)
    else:
        vibe = _local_vibe(messages)
        note = '(Heuristic local — adaugă GROQ_API_KEY pentru analiză AI.)'

    return JsonResponse({
        'vibe': vibe,
        'count': len(messages),
        'note': note,
    })


# ── Local heuristic fallbacks ─────────────────────────────────────────────────


def _local_summary(messages: list) -> str:
    words: Counter = Counter()
    for m in messages:
        for w in m.get('text', '').lower().split():
            w = w.strip('!?.,:;()[]')
            if len(w) > 2 and w not in _STOP_WORDS:
                words[w] += 1

    top = [w for w, _ in words.most_common(5)]
    count = len(messages)
    topics = ', '.join(top) if top else 'diverse subiecte'
    return (
        f'Pe baza ultimelor {count} mesaje, chat-ul discută despre: {topics}. '
        f'(Rezumat local — adaugă GROQ_API_KEY în .env pentru rezumat AI cu Llama.)'
    )


def _local_questions(messages: list) -> list[str]:
    seen: set[str] = set()
    questions: list[str] = []
    for m in messages:
        text = (m.get('text') or '').strip()
        if '?' not in text or len(text) < 6:
            continue
        norm = text.lower()
        if norm in seen:
            continue
        seen.add(norm)
        user = m.get('username', '?')
        questions.append(f'{user}: {text}')
        if len(questions) >= 15:
            break
    return questions


def _local_vibe(messages: list) -> str:
    if not messages:
        return 'Linişte'

    total_chars = 0
    upper_chars = 0
    laugh = 0
    questions = 0
    hype_emotes = 0
    negative = 0

    LAUGH = {'lul', 'lmao', 'lol', 'kek', 'omegalul', 'kekw', 'haha', 'xd'}
    HYPE_EMOTES = {'pog', 'pogchamp', 'poggers', 'letsgo', 'ez', 'gg', 'w'}
    NEG = {'trash', 'bad', 'l', 'cringe', 'hate', 'mid', 'boring', 'ratio'}

    for m in messages:
        text = m.get('text', '') or ''
        non_space = [c for c in text if not c.isspace()]
        total_chars += len(non_space)
        upper_chars += sum(1 for c in non_space if c.isupper())
        questions += text.count('?')
        words = {w.lower().strip('!?.,:;()[]') for w in text.split()}
        if words & LAUGH:        laugh += 1
        if words & HYPE_EMOTES:  hype_emotes += 1
        if words & NEG:          negative += 1

    n = len(messages)
    caps_ratio = (upper_chars / total_chars) if total_chars else 0
    laugh_ratio = laugh / n
    hype_ratio = hype_emotes / n
    neg_ratio = negative / n
    q_ratio = questions / n

    if neg_ratio > 0.20:
        return 'Toxic / Salty'
    if hype_ratio > 0.20 or caps_ratio > 0.45:
        return 'Hype / Excitement'
    if laugh_ratio > 0.20:
        return 'Comic / Lol Wave'
    if q_ratio > 0.30:
        return 'Confuzie / Întrebări'
    return 'Chill'
