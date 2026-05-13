import json
import logging
import os
import signal
import threading
import time
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor

import pika
import redis
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

logger = logging.getLogger(__name__)

RABBITMQ_HOST  = os.environ.get('RABBITMQ_HOST', 'localhost')
RABBITMQ_USER  = os.environ.get('RABBITMQ_USER', 'guest')
RABBITMQ_PASS  = os.environ.get('RABBITMQ_PASS', 'guest')
RABBITMQ_QUEUE = 'live_chat_queue'
REDIS_HOST     = os.environ.get('REDIS_HOST', 'localhost')
MAX_WORKERS    = int(os.environ.get('MAX_WORKERS', 4))
CHAT_BUFFER_SIZE = 100

# ── Twitch / gaming lexicon extension ─────────────────────────────────────────
# VADER tratează emote-urile şi slang-ul de Twitch ca scor 0 (neutre). Le mapăm
# manual la valenţe între -4 şi +4. Cheile sunt LOWERCASE — VADER face lookup
# pe `item.lower()` aşa că prinde "Pog", "POG", "pog" uniform; iar versiunea
# ALL CAPS într-o propoziţie mixt-case primeşte automat boost de intensificator.
TWITCH_LEXICON = {
    # Foarte pozitive — hype, win, râs
    'pog': 3.0, 'pogs': 2.5, 'pogchamp': 3.0, 'poggers': 3.0, 'pogu': 3.0,
    'kek': 2.5, 'kekw': 2.5, 'lul': 2.0, 'lulw': 2.0, 'omegalul': 2.8,
    '5head': 2.5, 'peepohappy': 3.0, 'pepelaugh': 2.5,
    'w': 2.5, 'ws': 2.5, 'ez': 2.0, 'clap': 2.5, 'clapclap': 2.5,
    'gg': 2.0, 'wp': 2.0, 'ggwp': 2.5,
    'goated': 3.0, 'goat': 2.5, 'cracked': 2.5,
    'gigachad': 3.5, 'chad': 2.0, 'based': 2.5, 'banger': 2.5,
    'letsgo': 3.0, 'letsgoo': 3.0, 'letsgooo': 3.0,
    'hype': 2.5, 'hypers': 2.5, 'sheesh': 2.0,
    'fire': 2.0, 'insane': 2.0, 'nuts': 1.8,

    # Foarte negative — pierdere, frică, tristeţe, cringe
    'l': -2.5, 'ls': -2.5, 'f': -2.0,
    'monkas': -2.5, 'monkaw': -3.0, 'monkahmm': -1.5, 'monkagiga': -2.0,
    'pepehands': -2.5, 'sadge': -2.5, 'feelsbadman': -2.5, 'feelsbad': -2.5,
    'kekwait': -1.5, 'weirdchamp': -2.5, 'weirdge': -2.0,
    'residentsleeper': -2.5, 'cringe': -2.5, 'cringey': -2.5,
    'throw': -2.5, 'throwing': -2.5, 'griefing': -2.5, 'griefer': -2.0,
    'lag': -2.0, 'laggy': -2.0, 'lagging': -2.0,
    'trash': -3.0, 'garbage': -2.5, 'mid': -1.5, 'ratio': -2.0,
    'yikes': -2.0, 'oof': -1.5,
    'tilted': -2.0, 'salty': -2.0, 'malding': -1.8,
    'rigged': -2.0, 'scuffed': -1.5, 'bots': -1.5,

    # Sarcasm / ambivalent — semnal mai slab
    'kappa': -0.5, 'copium': -0.8, 'jebaited': -1.0,
    'doubt': -0.5, 'sus': -1.0,
}

_analyzer = SentimentIntensityAnalyzer()
_analyzer.lexicon.update(TWITCH_LEXICON)

_redis_client = redis.Redis(host=REDIS_HOST, port=6379, decode_responses=True)

# ── Per-channel stats state ───────────────────────────────────────────────────

_lock = threading.Lock()
_word_counter: Counter = Counter()
_ts_deque: deque = deque(maxlen=1000)   # timestamps for velocity
_recent_msgs: deque = deque(maxlen=500) # (ts, username, text, abs_score, is_spam) — for tooltip
_last_msg_per_user: dict = {}           # spam detection
_msg_count: int = 0

STOP_WORDS = {
    'the', 'a', 'an', 'is', 'it', 'in', 'on', 'at', 'to', 'for', 'of',
    'and', 'or', 'but', 'not', 'with', 'this', 'that', 'are', 'was',
    'be', 'have', 'has', 'had', 'do', 'did', 'will', 'would', 'can',
    'could', 'should', 'me', 'my', 'you', 'your', 'he', 'she', 'we',
    'they', 'them', 'their', 'our', 'its', 'im', 'just', 'so', 'get',
    'got', 'ok', 'i', 'u', 'r', 'ur',
}

GAMEPLAY_KEYWORDS = {
    'gg', 'clutch', 'build', 'strat', 'meta', 'buff', 'nerf', 'op',
    'broken', 'aim', 'play', 'game', 'skill', 'heal', 'damage', 'tank',
    'carry', 'win', 'lose', 'kill', 'die', 'respawn', 'cooldown', 'combo',
    'ranked', 'queue', 'draft', 'pick', 'ban', 'push', 'rotate', 'farm',
}


def _classify_sentiment(text: str) -> tuple[str, float]:
    compound = _analyzer.polarity_scores(text)['compound']
    if compound >= 0.05:
        return 'POSITIVE', compound
    if compound <= -0.05:
        return 'NEGATIVE', compound
    return 'NEUTRAL', compound


def _classify_theme(text: str, username: str) -> str:
    stripped = text.strip()

    with _lock:
        prev = _last_msg_per_user.get(username)
        _last_msg_per_user[username] = stripped

    if prev == stripped:
        return 'SPAM'

    non_space = stripped.replace(' ', '')
    if non_space and sum(c.isupper() for c in non_space) / len(non_space) > 0.6 and len(stripped) > 2:
        return 'HYPE'

    lower_words = {w.lower().strip('!?.,:;()[]') for w in stripped.split()}
    if lower_words & GAMEPLAY_KEYWORDS:
        return 'TECHNICAL'

    return 'CHAT'


def _update_stats(text: str) -> None:
    words: list[str] = []
    for raw in text.split():
        clean = raw.lower().strip('!?.,:;()[]')
        if not clean:
            continue
        digit_count = sum(1 for c in clean if c.isdigit())
        if digit_count >= 2 or (len(clean) > 2 and clean not in STOP_WORDS):
            words.append(clean)
    with _lock:
        _word_counter.update(words)
        _ts_deque.append(time.time())


def _get_velocity() -> float:
    cutoff = time.time() - 10
    with _lock:
        recent = sum(1 for t in _ts_deque if t > cutoff)
    return round(recent / 10.0, 1)


def _get_top_words(n: int = 10) -> list[list]:
    with _lock:
        return [[w, c] for w, c in _word_counter.most_common(n)]


def _get_window_stats(n: int = 100) -> dict | None:
    """Heuristic, AI-free metrics computed on the last `n` processed messages.

    Returns unique_ratio (% unique chatters), caps_ratio (% uppercase letters
    over total alpha letters) and top-3 most prolific chatters.
    """
    with _lock:
        recent = list(_recent_msgs)[-n:]
    if not recent:
        return None

    users = [m[1] for m in recent]
    texts = [m[2] for m in recent]

    unique_ratio = round(len(set(users)) / len(users) * 100, 1)

    total_alpha = 0
    total_upper = 0
    for t in texts:
        for c in t:
            if c.isalpha():
                total_alpha += 1
                if c.isupper():
                    total_upper += 1
    caps_ratio = round(total_upper / total_alpha * 100, 1) if total_alpha else 0.0

    mvps = [[u, c] for u, c in Counter(users).most_common(3)]

    return {
        'unique_ratio': unique_ratio,
        'caps_ratio':   caps_ratio,
        'mvps':         mvps,
        'window_size':  len(recent),
    }


def _get_representative(window_sec: float = 2.0) -> dict | None:
    """Return the most representative message from the last `window_sec` seconds.

    Prefers the non-spam message with the highest |compound score|; falls back
    to the longest non-spam message; finally falls back to any recent message.
    """
    cutoff = time.time() - window_sec
    with _lock:
        recent = [m for m in _recent_msgs if m[0] > cutoff]
    if not recent:
        return None

    non_spam = [m for m in recent if not m[4]]
    pool = non_spam or recent

    by_score = max(pool, key=lambda m: m[3])
    if by_score[3] > 0:
        chosen = by_score
    else:
        chosen = max(pool, key=lambda m: len(m[2]))

    return {'username': chosen[1], 'text': chosen[2]}


def _process_and_publish(data: dict) -> None:
    global _msg_count

    text     = data.get('text', '')
    username = data.get('username', '')
    channel  = data.get('channel', '')

    sentiment, score = _classify_sentiment(text)
    theme            = _classify_theme(text, username)
    _update_stats(text)
    velocity         = _get_velocity()

    is_spam = theme == 'SPAM'
    with _lock:
        _recent_msgs.append((time.time(), username, text, abs(score), is_spam))
        _msg_count += 1
        count = _msg_count

    representative = _get_representative()

    result: dict = {
        'type':      'message',
        'username':  username,
        'text':      text,
        'channel':   channel,
        'sentiment': sentiment,
        'score':     round(score, 4),
        'theme':     theme,
        'velocity':  velocity,
        'representative': representative,
    }

    if count % 8 == 0:
        result['top_words'] = _get_top_words(10)
    if count % 5 == 0:
        stats = _get_window_stats(100)
        if stats:
            result['window_stats'] = stats

    buf_key = f'chat_buffer:{channel}'
    _redis_client.lpush(buf_key, json.dumps({'username': username, 'text': text}))
    _redis_client.ltrim(buf_key, 0, CHAT_BUFFER_SIZE - 1)

    _redis_client.publish(f'chat_updates:{channel}', json.dumps(result))


def _connect_rabbitmq(retries: int = 12, delay: int = 5) -> pika.BlockingConnection:
    credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
    params = pika.ConnectionParameters(
        host=RABBITMQ_HOST,
        credentials=credentials,
        heartbeat=600,
        blocked_connection_timeout=300,
    )
    for attempt in range(1, retries + 1):
        try:
            conn = pika.BlockingConnection(params)
            logger.info('Conectat la RabbitMQ (%d/%d)', attempt, retries)
            return conn
        except pika.exceptions.AMQPConnectionError as exc:
            logger.warning('RabbitMQ indisponibil (%d/%d) — reîncerc în %ds: %s',
                           attempt, retries, delay, exc)
            if attempt == retries:
                raise
            time.sleep(delay)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    logger.info('Worker pornit — %d thread-uri, VADER + metrici live', MAX_WORKERS)

    connection = _connect_rabbitmq()
    channel    = connection.channel()
    channel.queue_declare(queue=RABBITMQ_QUEUE, durable=True)
    channel.basic_qos(prefetch_count=MAX_WORKERS)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:

        def on_message(ch, method, _props, body):
            try:
                data = json.loads(body)
            except json.JSONDecodeError as exc:
                logger.error('Mesaj malformat: %s', exc)
                ch.basic_ack(delivery_tag=method.delivery_tag)
                return

            def on_done(future):
                try:
                    future.result()
                except Exception as exc:
                    logger.error('Eroare procesare: %s', exc)
                finally:
                    connection.add_callback_threadsafe(
                        lambda: ch.basic_ack(delivery_tag=method.delivery_tag)
                    )

            executor.submit(_process_and_publish, data).add_done_callback(on_done)

        def _shutdown(signum, _frame):
            logger.info('Semnal %d — oprire gracefulă...', signum)
            channel.stop_consuming()

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)

        channel.basic_consume(queue=RABBITMQ_QUEUE, on_message_callback=on_message)
        logger.info("Ascult pe '%s'...", RABBITMQ_QUEUE)

        try:
            channel.start_consuming()
        finally:
            connection.close()
            logger.info('Worker oprit.')


if __name__ == '__main__':
    main()
