import json
import re

import redis
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from .ingestor import manager

_TWITCH_URL_RE = re.compile(
    r'^(?:https?://)?(?:www\.)?twitch\.tv/([a-zA-Z0-9_]{1,25})(?:[/?].*)?$'
)
_PLAIN_NAME_RE = re.compile(r'^[a-zA-Z0-9_]{1,25}$')

_redis_client = redis.Redis(host=settings.REDIS_HOST, port=6379, decode_responses=True)


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
    return JsonResponse({'channel': channel})


@require_http_methods(['POST'])
def stop_stream(request):
    manager.stop()
    return JsonResponse({'stopped': True})


def stream_status(request):
    return JsonResponse({'channel': manager.active_channel})


@require_http_methods(['POST'])
def generate_summary(request):
    channel = manager.active_channel
    if not channel:
        return JsonResponse({'error': 'Niciun canal activ.'}, status=400)

    raw_messages = _redis_client.lrange(f'chat_buffer:{channel}', 0, 99)
    if not raw_messages:
        return JsonResponse({'error': 'Nu există mesaje în buffer încă.'}, status=400)

    messages = []
    for m in raw_messages:
        try:
            messages.append(json.loads(m))
        except json.JSONDecodeError:
            pass

    groq_key = getattr(settings, 'GROQ_API_KEY', '')

    if groq_key:
        summary = _groq_summary(messages, groq_key)
    else:
        summary = _local_summary(messages)

    return JsonResponse({'summary': summary, 'count': len(messages)})


def _groq_summary(messages: list, api_key: str) -> str:
    try:
        from groq import Groq
        chat_text = '\n'.join(
            f"{m.get('username', '?')}: {m.get('text', '')}"
            for m in messages
        )
        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model='llama-3.1-8b-instant',
            max_tokens=150,
            messages=[{
                'role': 'user',
                'content': (
                    'Summarize in 1-2 sentences what the main topic of discussion is '
                    'in this Twitch chat. Ignore spam and meaningless emotes.\n\n'
                    f'Messages:\n{chat_text}'
                ),
            }],
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        return f'[Eroare Groq: {exc}] ' + _local_summary(messages)


def _local_summary(messages: list) -> str:
    from collections import Counter
    stop = {
        'the', 'a', 'an', 'is', 'it', 'in', 'on', 'at', 'to', 'for', 'of',
        'and', 'or', 'but', 'not', 'with', 'this', 'that', 'are', 'was',
        'i', 'u', 'r', 'ur', 'im', 'so', 'just', 'me', 'my', 'you', 'your',
    }
    words: Counter = Counter()
    for m in messages:
        for w in m.get('text', '').lower().split():
            w = w.strip('!?.,:;()[]')
            if len(w) > 2 and w not in stop:
                words[w] += 1

    top = [w for w, _ in words.most_common(5)]
    count = len(messages)
    topics = ', '.join(top) if top else 'diverse subiecte'
    return (
        f'Pe baza ultimelor {count} mesaje, chat-ul discută despre: {topics}. '
        f'(Rezumat local — adaugă GROQ_API_KEY în .env pentru rezumat AI cu Llama.)'
    )
