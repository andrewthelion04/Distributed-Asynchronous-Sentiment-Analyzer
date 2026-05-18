import json
import logging
import queue
import random
import re
import socket
import threading
import time

import pika

logger = logging.getLogger(__name__)

TWITCH_IRC_HOST = 'irc.chat.twitch.tv'
TWITCH_IRC_PORT = 6667
RABBITMQ_QUEUE = 'live_chat_queue'

# :user!user@user.tmi.twitch.tv PRIVMSG #channel :message text
_PRIVMSG_RE = re.compile(
    r'^:(?P<user>\w+)!\w+@\w+\.tmi\.twitch\.tv PRIVMSG #(?P<channel>\w+) :(?P<text>.+)$'
)


def _pika_publisher(msg_queue: queue.Queue, host: str, user: str, passwd: str) -> None:
    """Runs in its own thread. Maintains one persistent pika connection."""
    credentials = pika.PlainCredentials(user, passwd)
    params = pika.ConnectionParameters(
        host=host,
        credentials=credentials,
        heartbeat=60,
        blocked_connection_timeout=300,
    )

    while True:
        try:
            conn = pika.BlockingConnection(params)
            ch = conn.channel()
            ch.queue_declare(queue=RABBITMQ_QUEUE, durable=True)
            logger.info('[ingestor-pika] conectat la RabbitMQ')

            while True:
                try:
                    item = msg_queue.get(timeout=1)
                except queue.Empty:
                    conn.process_data_events()
                    continue

                if item is None:
                    conn.close()
                    return

                try:
                    ch.basic_publish(
                        exchange='',
                        routing_key=RABBITMQ_QUEUE,
                        body=json.dumps(item),
                        properties=pika.BasicProperties(delivery_mode=2),
                    )
                except pika.exceptions.AMQPError as exc:
                    logger.warning('[ingestor-pika] eroare publish: %s', exc)
                    msg_queue.put(item)  # re-queue mesajul
                    break

            conn.close()

        except pika.exceptions.AMQPConnectionError as exc:
            logger.warning('[ingestor-pika] reconectare RabbitMQ în 3s: %s', exc)
            time.sleep(3)


def _twitch_reader(channel: str, msg_queue: queue.Queue, stop_event: threading.Event) -> None:
    """Runs in its own thread. Connects to Twitch IRC and feeds msg_queue."""
    nick = f'justinfan{random.randint(10000, 99999)}'

    while not stop_event.is_set():
        try:
            with socket.create_connection((TWITCH_IRC_HOST, TWITCH_IRC_PORT), timeout=15) as sock:
                sock.settimeout(5)
                buf = ''

                sock.sendall(
                    f'PASS SCHMOOZE\r\nNICK {nick}\r\nJOIN #{channel}\r\n'.encode()
                )
                logger.info('[ingestor-twitch] conectat la #%s', channel)

                while not stop_event.is_set():
                    try:
                        chunk = sock.recv(4096).decode('utf-8', errors='replace')
                    except socket.timeout:
                        continue
                    if not chunk:
                        logger.warning('[ingestor-twitch] conexiune inchisa de server')
                        break

                    buf += chunk
                    while '\r\n' in buf:
                        line, buf = buf.split('\r\n', 1)
                        if line.startswith('PING'):
                            sock.sendall(('PONG' + line[4:] + '\r\n').encode())
                            continue
                        _handle_line(line, channel, msg_queue)

        except OSError as exc:
            if stop_event.is_set():
                break
            logger.warning('[ingestor-twitch] eroare socket, reconectare în 5s: %s', exc)
            time.sleep(5)

    logger.info('[ingestor-twitch] oprit')


def _handle_line(line: str, channel: str, msg_queue: queue.Queue) -> None:
    m = _PRIVMSG_RE.match(line)
    if not m:
        return

    payload = {
        'username': m.group('user'),
        'text': m.group('text'),
        'channel': channel,
    }
    try:
        msg_queue.put_nowait(payload)
    except queue.Full:
        pass  # drop daca coada e plina (backpressure)


class IngestorManager:
    """Singleton care gestioneaza ingestorul activ curent."""

    def __init__(self):
        self._lock = threading.Lock()
        self._stop_event: threading.Event | None = None
        self._msg_queue: queue.Queue | None = None
        self._threads: list[threading.Thread] = []
        self._active_channel: str | None = None

    def start(self, channel: str, rabbitmq_host: str, rabbitmq_user: str, rabbitmq_pass: str) -> None:
        with self._lock:
            self._stop()

            self._stop_event = threading.Event()
            self._msg_queue = queue.Queue(maxsize=2000)
            self._active_channel = channel

            pika_thread = threading.Thread(
                target=_pika_publisher,
                args=(self._msg_queue, rabbitmq_host, rabbitmq_user, rabbitmq_pass),
                daemon=True,
                name='ingestor-pika',
            )
            twitch_thread = threading.Thread(
                target=_twitch_reader,
                args=(channel, self._msg_queue, self._stop_event),
                daemon=True,
                name='ingestor-twitch',
            )

            self._threads = [pika_thread, twitch_thread]
            pika_thread.start()
            twitch_thread.start()
            logger.info('Ingestor pornit pentru canalul #%s', channel)

    def stop(self) -> None:
        with self._lock:
            self._stop()

    def _stop(self) -> None:
        if self._stop_event:
            self._stop_event.set()
        if self._msg_queue:
            self._msg_queue.put(None)  # sentinel pentru pika_publisher
        self._threads = []
        self._stop_event = None
        self._msg_queue = None
        self._active_channel = None

    @property
    def active_channel(self) -> str | None:
        return self._active_channel


# Instanta globala — un singur ingestor per proces Django
manager = IngestorManager()
