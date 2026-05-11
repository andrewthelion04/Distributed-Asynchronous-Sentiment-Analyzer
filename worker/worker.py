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

_analyzer = SentimentIntensityAnalyzer()
_redis_client = redis.Redis(host=REDIS_HOST, port=6379, decode_responses=True)

# ── Per-channel stats state ───────────────────────────────────────────────────

_lock = threading.Lock()
_word_counter: Counter = Counter()
_ts_deque: deque = deque(maxlen=1000)   # timestamps for velocity
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
    words = [
        w.lower().strip('!?.,:;()[]')
        for w in text.split()
        if len(w) > 2 and w.lower() not in STOP_WORDS
    ]
    with _lock:
        _word_counter.update(w for w in words if w)
        _ts_deque.append(time.time())


def _get_velocity() -> float:
    cutoff = time.time() - 10
    with _lock:
        recent = sum(1 for t in _ts_deque if t > cutoff)
    return round(recent / 10.0, 1)


def _get_top_words(n: int = 10) -> list[list]:
    with _lock:
        return [[w, c] for w, c in _word_counter.most_common(n)]


def _process_and_publish(data: dict) -> None:
    global _msg_count

    text     = data.get('text', '')
    username = data.get('username', '')
    channel  = data.get('channel', '')

    sentiment, score = _classify_sentiment(text)
    theme            = _classify_theme(text, username)
    _update_stats(text)
    velocity         = _get_velocity()

    with _lock:
        _msg_count += 1
        count = _msg_count

    result: dict = {
        'type':      'message',
        'username':  username,
        'text':      text,
        'channel':   channel,
        'sentiment': sentiment,
        'score':     round(score, 4),
        'theme':     theme,
        'velocity':  velocity,
    }

    if count % 8 == 0:
        result['top_words'] = _get_top_words(10)

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
