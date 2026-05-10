import json
import logging
import multiprocessing
import os
import signal
import sqlite3
import time
from concurrent.futures import ProcessPoolExecutor

import pika

logger = logging.getLogger(__name__)

RABBITMQ_HOST  = os.environ.get('RABBITMQ_HOST', 'localhost')
RABBITMQ_USER  = os.environ.get('RABBITMQ_USER', 'guest')
RABBITMQ_PASS  = os.environ.get('RABBITMQ_PASS', 'guest')
RABBITMQ_QUEUE = 'reviews'
DB_PATH        = os.environ.get('DB_PATH', '/db/db.sqlite3')
# Limitat la 2 — fiecare proces ține modelul DistilBERT (~500 MB) în RAM
MAX_WORKERS    = int(os.environ.get('MAX_WORKERS', min(2, os.cpu_count() or 1)))


# ── Per-process model ─────────────────────────────────────────────────────────
# Fiecare proces din pool încarcă modelul o singură dată la pornire.
# Evităm re-încărcarea la fiecare inferență și serializarea modelului
# prin rețeaua inter-proces.

_pipeline = None


def _init_worker() -> None:
    global _pipeline
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] worker-process: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    from ml_model import load_model
    _pipeline = load_model()


def _run_inference(text: str) -> str:
    from ml_model import predict
    return predict(text, _pipeline)


# ── Database ──────────────────────────────────────────────────────────────────

def _db_update(review_id: int, status: str, sentiment: str | None = None) -> None:
    """
    Update direct prin sqlite3 — fără ORM Django în worker.
    Fiecare apel deschide și închide propria conexiune (thread-safe).
    """
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        conn.execute(
            """UPDATE analyzer_review
               SET    status    = ?,
                      sentiment = ?,
                      updated_at = datetime('now')
               WHERE  id = ?""",
            (status, sentiment, review_id),
        )
        conn.commit()
    finally:
        conn.close()


# ── RabbitMQ ──────────────────────────────────────────────────────────────────

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
            logger.info("Conectat la RabbitMQ (încercare %d/%d)", attempt, retries)
            return conn
        except pika.exceptions.AMQPConnectionError as exc:
            logger.warning(
                "RabbitMQ indisponibil (%d/%d) — reîncerc în %ds: %s",
                attempt, retries, delay, exc,
            )
            if attempt == retries:
                raise
            time.sleep(delay)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    logger.info("Worker pornit — pool de %d procese", MAX_WORKERS)

    connection = _connect_rabbitmq()
    channel    = connection.channel()
    channel.queue_declare(queue=RABBITMQ_QUEUE, durable=True)

    # Nu trimite mai multe mesaje unui consumer decât procesele disponibile
    channel.basic_qos(prefetch_count=MAX_WORKERS)

    # spawn evită conflictele torch cu fork (stare internă incompletă după fork)
    mp_ctx = multiprocessing.get_context('spawn')
    with ProcessPoolExecutor(max_workers=MAX_WORKERS, initializer=_init_worker, mp_context=mp_ctx) as executor:

        def on_message(ch, method, _properties, body):
            # ── 1. Parsare mesaj ──────────────────────────────────────────
            try:
                data      = json.loads(body)
                review_id = int(data['id'])
                text      = str(data['text'])
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                logger.error("Mesaj malformat, ignorat: %s | body=%s", exc, body)
                ch.basic_ack(delivery_tag=method.delivery_tag)
                return

            # ── 2. Marchează recenzia ca în procesare ─────────────────────
            logger.info("Recenzie #%d preluată", review_id)
            _db_update(review_id, 'PROCESSING')

            # ── 3. Trimite inferența într-un proces din pool ───────────────
            future = executor.submit(_run_inference, text)

            # ── 4. Callback la finalizare (rulează în thread-ul intern al pool-ului)
            def on_done(f, _id=review_id, _tag=method.delivery_tag):
                try:
                    sentiment = f.result()
                    _db_update(_id, 'PROCESSED', sentiment)
                    logger.info("Recenzie #%d → %s", _id, sentiment)
                except Exception as exc:
                    logger.error("Inferență eșuată pentru recenzia #%d: %s", _id, exc)
                    _db_update(_id, 'FAILED')
                finally:
                    # basic_ack trebuie apelat pe thread-ul conexiunii pika,
                    # nu pe cel al pool-ului — folosim add_callback_threadsafe.
                    connection.add_callback_threadsafe(
                        lambda: ch.basic_ack(delivery_tag=_tag)
                    )

            future.add_done_callback(on_done)

        # ── Oprire gracefulă la SIGTERM / SIGINT ──────────────────────────
        def _shutdown(signum, _frame):
            logger.info("Semnal %d primit — oprire gracefulă...", signum)
            channel.stop_consuming()

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)

        channel.basic_consume(queue=RABBITMQ_QUEUE, on_message_callback=on_message)
        logger.info("Worker ascultă pe coada '%s'...", RABBITMQ_QUEUE)

        try:
            channel.start_consuming()
        finally:
            connection.close()
            logger.info("Conexiune închisă. Worker oprit.")


if __name__ == '__main__':
    main()
