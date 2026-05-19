# Technical Reference — Distributed Asynchronous Sentiment Analyzer

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Repository Layout](#2-repository-layout)
3. [Infrastructure — Docker Compose](#3-infrastructure--docker-compose)
4. [Service: Web (Django ASGI)](#4-service-web-django-asgi)
   - 4.1 [ASGI Entry Point](#41-asgi-entry-point)
   - 4.2 [Django Settings](#42-django-settings)
   - 4.3 [URL Routing](#43-url-routing)
   - 4.4 [Views — HTTP API](#44-views--http-api)
   - 4.5 [Ingestor — Twitch IRC](#45-ingestor--twitch-irc)
   - 4.6 [WebSocket Consumer](#46-websocket-consumer)
   - 4.7 [WebSocket URL Routing](#47-websocket-url-routing)
5. [Service: Worker](#5-service-worker)
   - 5.1 [VADER + Twitch Lexicon](#51-vader--twitch-lexicon)
   - 5.2 [Per-Channel In-Memory State](#52-per-channel-in-memory-state)
   - 5.3 [Analysis Functions](#53-analysis-functions)
   - 5.4 [Message Processing Pipeline](#54-message-processing-pipeline)
   - 5.5 [RabbitMQ Consumer Loop](#55-rabbitmq-consumer-loop)
6. [Service: RabbitMQ](#6-service-rabbitmq)
7. [Service: Redis](#7-service-redis)
8. [Frontend](#8-frontend)
   - 8.1 [HTML Structure](#81-html-structure)
   - 8.2 [CSS Architecture](#82-css-architecture)
   - 8.3 [JavaScript Modules](#83-javascript-modules)
9. [API Reference](#9-api-reference)
   - 9.1 [HTTP Endpoints](#91-http-endpoints)
   - 9.2 [WebSocket Protocol](#92-websocket-protocol)
   - 9.3 [WebSocket Message Schema](#93-websocket-message-schema)
10. [Data Schemas](#10-data-schemas)
11. [Environment Variables](#11-environment-variables)
12. [Dependencies](#12-dependencies)

---

## 1. Project Overview

The Distributed Asynchronous Sentiment Analyzer is a real-time Twitch chat monitoring dashboard. It connects anonymously to any public Twitch channel via IRC, routes every chat message through a distributed processing pipeline (RabbitMQ → Worker → Redis), and pushes enriched analytics to an interactive browser dashboard over a persistent WebSocket connection.

**Core capabilities:**

| Capability | Implementation |
|---|---|
| Real-time chat ingestion | Raw TCP socket to Twitch IRC (no OAuth) |
| Asynchronous message queueing | RabbitMQ durable queue |
| Sentiment analysis | vaderSentiment + custom Twitch lexicon |
| Theme classification | Heuristic rules (HYPE / SPAM / TECHNICAL / CHAT) |
| Live metrics push | Redis pub/sub → Django Channels WebSocket |
| Chat buffer for LLM | Redis LPUSH/LTRIM sliding window (100 messages) |
| On-demand LLM analysis | Groq API (Llama 3.1 8B) with local fallbacks |
| Dynamic background | Streamer offline image via decapi.me |
| Dark/light theme | CSS custom properties, persisted to localStorage |

---

## 2. Repository Layout

```
.
├── docker-compose.yml
├── .env                          # secrets — never committed
├── backend/
│   ├── django_app/
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── manage.py
│   │   ├── analyzer/             # Django app
│   │   │   ├── consumers.py      # AsyncWebsocketConsumer
│   │   │   ├── ingestor.py       # IngestorManager + IRC threads
│   │   │   ├── routing.py        # WebSocket URL patterns
│   │   │   ├── urls.py           # HTTP URL patterns
│   │   │   └── views.py          # All HTTP views + LLM endpoints
│   │   └── sentiment_project/    # Django project
│   │       ├── asgi.py           # ASGI entry — HTTP + WS routing
│   │       ├── settings.py
│   │       ├── urls.py
│   │       └── wsgi.py
│   └── worker/
│       ├── Dockerfile
│       ├── requirements.txt
│       └── worker.py             # RabbitMQ consumer + analysis
└── frontend/
    ├── templates/
    │   └── index.html            # Django template (served by web)
    └── static/
        ├── css/
        │   ├── theme.css         # CSS custom properties, dark/light
        │   ├── layout.css        # Macro layout, grids, background
        │   ├── components.css    # Buttons, action bar, modal
        │   ├── chat.css          # Chat log, badges, message styles
        │   ├── stats.css         # Stats panel, progress bars, MVPs
        │   └── charts.css        # Chart containers
        └── js/
            ├── theme.js          # Theme toggle + chart re-tinting
            ├── charts.js         # Chart.js instances, hype timer
            ├── stats.js          # Counters, top words, window stats, reset
            ├── chat.js           # Render, filter, pause/resume
            ├── websocket.js      # WebSocket lifecycle, status indicator
            ├── background.js     # Banner/avatar fetch, dynamic BG
            ├── llm-actions.js    # Modal + fetch for TL;DR/Questions/Vibe
            └── app.js            # Bootstrap: shared state, event wiring
```

The `frontend/` directory is mounted read-only into the `web` container at `/frontend` (see `docker-compose.yml` volumes).

---

## 3. Infrastructure — Docker Compose

Four services are declared in `docker-compose.yml`.

### 3.1 Service topology

```
┌─────────────────────────────────────────────────────┐
│                  docker network (default bridge)     │
│                                                      │
│  ┌──────────┐   AMQP    ┌──────────┐                 │
│  │  web:8000│──────────▶│ rabbitmq │◀──────────────┐  │
│  │  (Daphne)│   5672    │  :5672   │               │  │
│  └──────────┘           └──────────┘               │  │
│       │                                            │  │
│       │ Redis pub/sub + channel layer              │  │
│       ▼                                            │  │
│  ┌──────────┐                                      │  │
│  │  redis   │◀─────── worker publishes ────────────┘  │
│  │  :6379   │                                         │
│  └──────────┘   ▲                                     │
│                 │                                     │
│            ┌────────┐                                 │
│            │ worker │─────────────────────────────────┘
│            └────────┘  (consumes from rabbitmq,
│                         publishes to redis)           │
└─────────────────────────────────────────────────────┘
```

### 3.2 Service definitions

| Service | Image / Build | Exposed Ports | Health Check |
|---|---|---|---|
| `rabbitmq` | `rabbitmq:3.12-management` | 5672 (AMQP), 15672 (mgmt UI) | `rabbitmq-diagnostics ping` |
| `redis` | `redis:7-alpine` | 6379 | `redis-cli ping` |
| `web` | `./backend/django_app` | 8000 | — (depends on healthy rmq+redis) |
| `worker` | `./backend/worker` | — | — (depends on healthy rmq+redis) |

### 3.3 Startup sequence

`web` and `worker` both declare `depends_on` with `condition: service_healthy` for both `rabbitmq` and `redis`, so Docker Compose blocks their startup until health checks pass. The `web` container additionally runs `python manage.py migrate` before launching Daphne.

### 3.4 Volume mounts

```yaml
web:
  volumes:
    - ./backend/django_app:/app       # live code reload in dev
    - ./frontend:/frontend:ro         # static files + templates
```

The `:ro` flag makes the frontend mount read-only inside the container for safety. The worker mounts `./backend/worker:/app` for the same live-reload benefit in development.

---

## 4. Service: Web (Django ASGI)

**Runtime:** Python 3.11, Django 4.2, Daphne 4.1, Django Channels 4.1

### 4.1 ASGI Entry Point

**File:** `backend/django_app/sentiment_project/asgi.py`

```python
application = ProtocolTypeRouter({
    'http': django_asgi_app,
    'websocket': AuthMiddlewareStack(URLRouter(websocket_urlpatterns)),
})
```

`ProtocolTypeRouter` is the Channels top-level dispatcher. It inspects the ASGI `scope['type']` field of every incoming connection and routes it to the appropriate sub-application:

- `http` → standard Django ASGI application (handles all HTTP requests)
- `websocket` → Django Channels consumer, wrapped in `AuthMiddlewareStack` which populates `scope['user']` for session-authenticated users (unused currently but allows future auth)

Daphne is the ASGI server that runs this application. Unlike Gunicorn (WSGI), Daphne handles long-lived connections natively.

### 4.2 Django Settings

**File:** `backend/django_app/sentiment_project/settings.py`

Key settings:

| Setting | Value / Source | Purpose |
|---|---|---|
| `SECRET_KEY` | env `DJANGO_SECRET_KEY` | CSRF signing, session security |
| `DEBUG` | env `DEBUG` | Template debug, error pages |
| `INSTALLED_APPS` | includes `channels`, `analyzer` | Registers Channels and the app |
| `ASGI_APPLICATION` | `sentiment_project.asgi.application` | Tells Channels which ASGI app to use |
| `CHANNEL_LAYERS` | Redis at `REDIS_HOST:6379` | Channels internal message bus (used by `channels_redis`) |
| `TEMPLATES.DIRS` | `FRONTEND_DIR/templates` | Finds `index.html` outside the app package |
| `STATICFILES_DIRS` | `FRONTEND_DIR/static` | Serves JS/CSS from the frontend directory |
| `WHITENOISE_USE_FINDERS` | `True` | WhiteNoise serves files from `STATICFILES_DIRS` without `collectstatic` |
| `RABBITMQ_HOST/USER/PASS` | env vars | Passed to `IngestorManager.start()` |
| `REDIS_HOST` | env `REDIS_HOST` | Used by views for direct Redis access |
| `GROQ_API_KEY` | env `GROQ_API_KEY` | Groq LLM API (optional; falls back to heuristics) |

The `FRONTEND_DIR` path resolves differently depending on context:
- **Docker:** set explicitly to `/frontend` via `FRONTEND_DIR` env var
- **Local dev:** falls back to `BASE_DIR.parent.parent / 'frontend'` (repo root)

### 4.3 URL Routing

**Files:** `sentiment_project/urls.py` → `analyzer/urls.py`

```
GET  /                      → views.index
POST /api/start/            → views.start_stream
POST /api/stop/             → views.stop_stream
GET  /api/status/           → views.stream_status
POST /api/summary/          → views.generate_summary
POST /api/extract-questions/→ views.extract_questions
POST /api/vibe-check/       → views.vibe_check
```

All LLM endpoints are `POST` because they trigger a side-effectful operation (buffer read + API call). `stream_status` is `GET` since it only reads in-memory state.

### 4.4 Views — HTTP API

**File:** `backend/django_app/analyzer/views.py`

#### `_extract_channel(raw: str) -> str | None`

Accepts either a full Twitch URL or a plain username. Two regexes handle both forms:

```python
_TWITCH_URL_RE = re.compile(
    r'^(?:https?://)?(?:www\.)?twitch\.tv/([a-zA-Z0-9_]{1,25})(?:[/?].*)?$'
)
_PLAIN_NAME_RE = re.compile(r'^[a-zA-Z0-9_]{1,25}$')
```

Returns the channel name in lowercase, or `None` if neither pattern matches.

#### `_fetch_banner_url(channel: str) -> str | None`

Makes up to two sequential HTTP `GET` requests to `decapi.me` (a free Twitch data proxy):
1. `decapi.me/twitch/offline_image/{channel}` — the streamer's offline screen image
2. `decapi.me/twitch/banner/{channel}` — the profile banner

Returns the first URL that starts with `http`. If both fail or return non-URL text (e.g. "No offline image set."), returns `None`. Uses `httpx` with a 2.5-second timeout to avoid blocking.

#### `start_stream(request)`

1. Extracts the channel name.
2. Calls `manager.start(channel, ...)` — this stops any existing ingestor and starts two new threads.
3. Calls `_fetch_banner_url(channel)`.
4. Returns `{"channel": "...", "banner": "<url or null>"}`.

#### `stop_stream(request)`

Calls `manager.stop()` and returns `{"stopped": true}`.

#### `_get_buffer_messages() -> (messages, error_response)`

Helper shared by all three LLM endpoints. Reads up to 100 messages from the Redis key `chat_buffer:{channel}` (the circular buffer maintained by the worker). Returns a list of `{"username": ..., "text": ...}` dicts.

#### `_llm_call(prompt, messages, api_key, max_tokens) -> str`

Formats the buffer as `username: text` lines, then calls the Groq API with the `llama-3.1-8b-instant` model. Each LLM endpoint passes its own prompt.

#### LLM Endpoints

| Endpoint | Prompt intent | Max tokens | Fallback |
|---|---|---|---|
| `generate_summary` | 1-2 sentence topic summary | 150 | `_local_summary()` — top-5 frequent words |
| `extract_questions` | List questions addressed to streamer | 400 | `_local_questions()` — messages containing `?` |
| `vibe_check` | Single short vibe phrase | 20 | `_local_vibe()` — ratio-based heuristic |

All three endpoints degrade gracefully: if `GROQ_API_KEY` is absent or the API call raises, the local fallback runs and the response includes a `note` field explaining it.

### 4.5 Ingestor — Twitch IRC

**File:** `backend/django_app/analyzer/ingestor.py`

The ingestor bridges Twitch IRC and RabbitMQ. It runs entirely in the Django process as daemon threads, so it shares the process lifecycle without requiring a separate container.

#### Thread architecture

```
IngestorManager.start()
    │
    ├── Thread "ingestor-twitch"  (_twitch_reader)
    │       │
    │       │  raw IRC lines
    │       ▼
    │   queue.Queue(maxsize=2000)   ← in-memory buffer
    │       │
    └── Thread "ingestor-pika"   (_pika_publisher)
                │
                ▼
           RabbitMQ: live_chat_queue
```

#### `_twitch_reader(channel, msg_queue, stop_event)`

- Opens a raw TCP socket to `irc.chat.twitch.tv:6667`.
- Authenticates with `PASS SCHMOOZE` and `NICK justinfanNNNNN` (Twitch's anonymous-access convention — any `justinfan*` nickname is accepted without a valid password or token).
- Joins `#channel`.
- Reads in a loop, accumulating data into a string buffer, splitting on `\r\n`.
- Responds to `PING` lines with `PONG` to keep the connection alive.
- Parses `PRIVMSG` lines with `_PRIVMSG_RE` regex.
- On any socket error, sleeps 5 seconds and reconnects.
- Checks `stop_event` before each iteration.

**`_PRIVMSG_RE`** captures three named groups:

```
:user!user@user.tmi.twitch.tv PRIVMSG #channel :message text
 ^^^^                                   ^^^^^^^  ^^^^^^^^^^^^
 user                                   channel  text
```

#### `_pika_publisher(msg_queue, host, user, passwd)`

- Maintains a single persistent `pika.BlockingConnection`.
- Blocks on `msg_queue.get(timeout=1)`, calls `conn.process_data_events()` on timeout to service heartbeats.
- On AMQP error during publish, re-queues the message and breaks inner loop to reconnect.
- Sentinel value `None` in the queue signals shutdown.
- Queue declared as `durable=True`, messages as `delivery_mode=2` (persistent).

#### `IngestorManager`

Singleton (`manager`) created at module load. Thread-safe via `threading.Lock()`.

| Method | Behavior |
|---|---|
| `start(channel, ...)` | Stops any running ingestor first, then starts fresh threads |
| `stop()` | Sets stop event, enqueues `None` sentinel, clears state |
| `active_channel` | Property returning the current channel name or `None` |

### 4.6 WebSocket Consumer

**File:** `backend/django_app/analyzer/consumers.py`

```python
class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self._channel = self.scope['url_route']['kwargs']['channel']
        self._redis_ch = f'chat_updates:{self._channel}'
        await self.accept()
        self._redis = aioredis.from_url(f'redis://{REDIS_HOST}')
        self._pubsub = self._redis.pubsub()
        await self._pubsub.subscribe(self._redis_ch)
        self._listener = asyncio.create_task(self._listen())
```

On connection:
1. Extracts `channel` from the URL route kwargs.
2. Accepts the WebSocket immediately.
3. Opens an async Redis connection and subscribes to `chat_updates:{channel}`.
4. Spawns a background asyncio task `_listen()` that loops over incoming pub/sub messages and forwards them as-is to the WebSocket client.

On disconnect, the listener task is cancelled and all Redis resources are cleaned up.

`receive()` is a no-op — the browser never sends data upstream over this WebSocket.

The consumer is **channel-scoped**: each browser connects to `ws://.../ws/chat/{channel}/` and receives only messages published to that channel's Redis key. This correctly isolates multiple concurrent channel sessions.

### 4.7 WebSocket URL Routing

**File:** `backend/django_app/analyzer/routing.py`

```python
websocket_urlpatterns = [
    path('ws/chat/<str:channel>/', consumers.ChatConsumer.as_asgi()),
]
```

`<str:channel>` is a Django path converter that captures the channel name and makes it available in `scope['url_route']['kwargs']['channel']`.

---

## 5. Service: Worker

**File:** `backend/worker/worker.py`

**Runtime:** Python 3.11, pika 1.3.2, redis 5.0.7, vaderSentiment 3.3.2

The worker is a standalone Python process. It consumes messages from RabbitMQ, runs analysis, and publishes results to Redis.

### 5.1 VADER + Twitch Lexicon

```python
_analyzer = SentimentIntensityAnalyzer()
_analyzer.lexicon.update(TWITCH_LEXICON)
```

VADER's native lexicon contains ~7,500 English words with pre-assigned valence scores. It has no coverage for Twitch-specific vocabulary. The `TWITCH_LEXICON` dict extends it with ~70 gaming/streaming terms:

**Scoring scale:** −4.0 (maximally negative) to +4.0 (maximally positive)

| Category | Examples | Score range |
|---|---|---|
| Hype / win | `pog`, `pogchamp`, `letsgo`, `gigachad` | +2.0 to +3.5 |
| Laugh / humor | `kekw`, `lul`, `omegalul` | +2.0 to +2.8 |
| Positive outcome | `gg`, `ez`, `clap`, `banger` | +2.0 to +2.5 |
| Fear / anxiety | `monkas`, `monkaw`, `monkagiga` | −1.5 to −3.0 |
| Sadness | `sadge`, `pepehands`, `feelsbadman` | −2.5 |
| Negative outcome | `l`, `trash`, `garbage`, `ratio` | −2.0 to −3.0 |
| Sarcasm / doubt | `kappa`, `copium`, `sus` | −0.5 to −1.0 |

All keys are lowercase. VADER internally lowercases tokens before lexicon lookup, so `Pog`, `POG`, and `pog` all match. A message in ALL CAPS also receives VADER's built-in intensity boost.

**Compound score thresholds:**

| compound | Sentiment |
|---|---|
| ≥ 0.05 | POSITIVE |
| ≤ −0.05 | NEGATIVE |
| between | NEUTRAL |

### 5.2 Per-Channel In-Memory State

The worker holds global state (reset only on process restart):

| Variable | Type | Purpose |
|---|---|---|
| `_word_counter` | `Counter` | Cumulative word frequency since start |
| `_ts_deque` | `deque(maxlen=1000)` | Timestamps of the last 1000 messages (for velocity) |
| `_recent_msgs` | `deque(maxlen=500)` | Tuples `(ts, username, text, abs_score, is_spam)` for window metrics and tooltip |
| `_last_msg_per_user` | `dict` | Last message text per username (spam detection) |
| `_msg_count` | `int` | Global message counter (controls periodic metric emission) |

All access is protected by a single `threading.Lock` (`_lock`) since `ThreadPoolExecutor` workers run in separate threads.

### 5.3 Analysis Functions

#### `_classify_sentiment(text) -> (label, compound_score)`

Calls `_analyzer.polarity_scores(text)['compound']` and maps to POSITIVE / NEGATIVE / NEUTRAL.

#### `_classify_theme(text, username) -> str`

Priority order:

1. **SPAM** — if this user's last message was identical to the current one.
2. **HYPE** — if >60% of non-space characters are uppercase and message length >2.
3. **TECHNICAL** — if any word (lowercased, stripped of punctuation) intersects with `GAMEPLAY_KEYWORDS` (60 gaming terms).
4. **CHAT** — default.

#### `_update_stats(text)`

Tokenises `text`, strips punctuation, lowercases. Filters out: stop words, tokens with 2+ digit characters, tokens ≤2 characters. Updates `_word_counter` and appends the current timestamp to `_ts_deque`.

#### `_get_velocity() -> float`

Counts timestamps in `_ts_deque` from the last 10 seconds and divides by 10.0, giving a messages/second rate as a rolling 10-second average.

#### `_get_top_words(n=10) -> list[list]`

Returns `[[word, count], ...]` for the top `n` words from `_word_counter.most_common(n)`.

#### `_get_window_stats(n=100) -> dict`

Analyses the last `n` entries in `_recent_msgs`:

- **`unique_ratio`** — `len(set(users)) / len(users) * 100`. High values (>60%) indicate a diverse, organic audience rather than bots or one person spamming.
- **`caps_ratio`** — `total_uppercase_alpha / total_alpha * 100`. High values (>40-50%) correlate with hype, excitement, or rage.
- **`mvps`** — Top 3 usernames by message count in the window, as `[[username, count], ...]`.

#### `_get_representative() -> dict | None`

Finds the most "representative" message from the last 2 seconds. Used to populate the chart tooltip.

Selection logic:
1. Filters to messages from the last 2 seconds.
2. Prefers non-spam messages.
3. Within that pool, picks the message with the highest `|compound score|`.
4. If all scores are 0, falls back to the longest message.

### 5.4 Message Processing Pipeline

`_process_and_publish(data: dict)` is called in a thread-pool thread for each RabbitMQ message:

```
data = {"username": ..., "text": ..., "channel": ...}
  │
  ├─ _classify_sentiment()  → sentiment label + compound score
  ├─ _classify_theme()      → theme label
  ├─ _update_stats()        → updates _word_counter + _ts_deque
  ├─ _get_velocity()        → messages/sec
  ├─ Append to _recent_msgs
  │
  ├─ [every 8th message]  _get_top_words()   → included in result
  ├─ [every 5th message]  _get_window_stats() → included in result
  │
  ├─ _get_representative() → tooltip message snapshot
  │
  ├─ redis LPUSH chat_buffer:{channel}  (sliding window for LLM)
  ├─ redis LTRIM chat_buffer:{channel} 0 99  (keep last 100)
  │
  └─ redis PUBLISH chat_updates:{channel} <json result>
```

The periodic emission of `top_words` (every 8 messages) and `window_stats` (every 5 messages) reduces the WebSocket payload size — these are bulk metrics that don't need to update on every single message.

**Result JSON structure:**

```json
{
  "type": "message",
  "username": "...",
  "text": "...",
  "channel": "...",
  "sentiment": "POSITIVE|NEGATIVE|NEUTRAL",
  "score": 0.0000,
  "theme": "HYPE|SPAM|TECHNICAL|CHAT",
  "velocity": 0.0,
  "representative": {"username": "...", "text": "..."},
  "top_words": [["word", count], ...],     // present every 8th message
  "window_stats": {                         // present every 5th message
    "unique_ratio": 0.0,
    "caps_ratio": 0.0,
    "mvps": [["user", count], ...],
    "window_size": 100
  }
}
```

### 5.5 RabbitMQ Consumer Loop

```python
channel.basic_qos(prefetch_count=MAX_WORKERS)

with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
    def on_message(ch, method, _props, body):
        executor.submit(_process_and_publish, data).add_done_callback(on_done)

    channel.basic_consume(queue=RABBITMQ_QUEUE, on_message_callback=on_message)
    channel.start_consuming()
```

`prefetch_count=MAX_WORKERS` (default 4) tells RabbitMQ to deliver at most 4 unacknowledged messages at a time — matching the thread pool size so the consumer never gets further ahead than it can process. Acknowledgement (`basic_ack`) is sent inside `on_done`, called via `connection.add_callback_threadsafe` to safely cross the thread boundary back to pika's I/O thread.

`SIGTERM` and `SIGINT` both call `channel.stop_consuming()` for a graceful shutdown.

---

## 6. Service: RabbitMQ

**Image:** `rabbitmq:3.12-management`

**Queue:** `live_chat_queue`
- Declared `durable=True` on both the producer (ingestor) and consumer (worker) sides.
- Messages published with `delivery_mode=2` (persistent) — they survive a broker restart.

**Management UI:** `http://localhost:15672` with credentials from `.env`. The Queues → `live_chat_queue` view shows the real-time queue depth, useful for diagnosing backpressure.

The queue decouples ingestion rate from processing rate. If the worker falls behind (e.g. slow VADER analysis), messages accumulate in the queue rather than being dropped.

---

## 7. Service: Redis

**Image:** `redis:7-alpine`

Redis serves two completely independent roles:

### Role 1: Django Channels Channel Layer

Configured via `CHANNEL_LAYERS` in `settings.py`. This is the internal message bus for Django Channels' group messaging. In the current implementation it is not actively used for group messaging (the consumer bypasses Channels groups and subscribes directly to Redis pub/sub), but it is required by Django Channels as a backend.

### Role 2: Pub/Sub Bus for Real-Time Updates

**Channel name pattern:** `chat_updates:{channel_name}`

The worker calls `redis.publish('chat_updates:{channel}', json_result)` after processing each message. The `ChatConsumer`'s `_listen()` task calls `pubsub.listen()` in an async loop and forwards each message to the browser WebSocket.

### Role 3: Sliding Window Buffer for LLM

**Key name pattern:** `chat_buffer:{channel_name}`

Every processed message is prepended to this list:

```
LPUSH chat_buffer:channel  '{"username": ..., "text": ...}'
LTRIM chat_buffer:channel  0  99
```

`LPUSH` adds to the head (newest first). `LTRIM` keeps only indices 0–99 (100 items). `LRANGE chat_buffer:channel 0 99` in the LLM views retrieves the 100 most recent messages.

---

## 8. Frontend

The frontend is a single-page application served by Django as a rendered template (`index.html`). There is no build step — plain HTML, CSS, and vanilla JavaScript.

### 8.1 HTML Structure

`frontend/templates/index.html` is a Django template. It uses `{% load static %}` and `{% static '...' %}` tags to resolve static file URLs.

Key structural elements:

| Element | ID / Class | Purpose |
|---|---|---|
| `<div id="dynamic-background">` | — | Full-viewport blurred banner image |
| `<header>` | — | App title, theme toggle |
| `.input-row` | — | URL input, Start/Stop buttons |
| `#profile-bar` | — | Streamer avatar + name (hidden until connected) |
| `#status-bar` | `#ws-dot`, `#status-text` | WebSocket connection indicator |
| `.action-bar` | — | TL;DR, Questions, Vibe Check buttons |
| `.metrics-grid` | — | Hype Meter (line chart) + Top Words (bar chart) |
| `.content-grid` | — | Chat log, stats panel, sentiment panel |
| `#modal-overlay` | — | LLM results modal |

CSRF token and initial channel are injected via `data-*` attributes on `<body>`:

```html
<body data-csrf="{{ csrf_token }}" data-initial-channel="{{ active_channel|default:'' }}">
```

`active_channel` comes from the `index` view, which reads `manager.active_channel`. If an ingestor is already running (e.g. after a page refresh), the JS bootstrap in `app.js` automatically reconnects the WebSocket without the user pressing Start again.

### 8.2 CSS Architecture

CSS is split into six files loaded in order:

| File | Scope |
|---|---|
| `theme.css` | CSS custom properties (`:root` dark, `[data-theme="light"]`), box-model reset, body base |
| `layout.css` | Dynamic background, header, container, input row, profile bar, status bar, grid layouts |
| `components.css` | Buttons (`.action-btn`), action bar, modal overlay, filter chip, spinner |
| `chat.css` | Chat log (`#chat-log`), message entries, theme badges, highlight spans |
| `stats.css` | Stats panel, progress bars, MVP list, sentiment mini-cards |
| `charts.css` | Chart container heights, chart box styling |

All colors, backgrounds, and borders reference CSS custom properties from `theme.css`. Switching theme is a single attribute write on `<html>`:

```javascript
document.documentElement.setAttribute('data-theme', 'light');
```

No class toggling or style recalculation is needed because the entire design is defined in terms of `var(--...)` tokens.

### 8.3 JavaScript Modules

Scripts are loaded at the bottom of `<body>` in a specific order. Because there is no module bundler, each file declares functions in the global scope. `app.js` loads last and can call functions from all other files.

#### `theme.js`

- `getTheme()` — reads `localStorage.getItem('theme')` with `'dark'` as default.
- `applyTheme(theme)` — sets `data-theme` on `<html>`, updates toggle button icon, calls `syncChartsToTheme()`.
- `syncChartsToTheme()` — reads current CSS variables and pushes new colors into Chart.js dataset and scale config.
- `toggleTheme()` — flips theme, saves to localStorage.

#### `charts.js`

Initialises two Chart.js instances:

**Hype Meter** (`hypeChart`) — line chart, 30-point sliding window updated every 1 second by `setInterval`. Each second, `lastVelocity` and `lastRepresentative` are snapshotted into the data arrays, and the leftmost point is discarded. The custom tooltip `afterBody` callback reads `hypeMessages[idx]` to show the representative message on hover.

**Top Words** (`wordsChart`) — horizontal bar chart. `onClick` calls `toggleFilter(word)` for cross-filtering. Data is replaced wholesale when `updateTopWords()` is called (every 8th incoming message).

#### `stats.js`

- `updateSentimentCounters(sentiment)` — increments `counts[sentiment]` and `total`, recalculates percentages, updates progress bars.
- `updateTopWords(topWords)` — updates wordsChart data.
- `updateWindowStats(stats)` — updates unique ratio and caps ratio progress bars (with color classes `warn`/`bad`) and renders the MVP list.
- `resetDashboard()` — zeroes all counters, clears chat log, resets chart data, clears history and pause buffer.

#### `chat.js`

- `ingestMessage(data)` — the central message handler, called by `websocket.js` for every incoming WebSocket frame. Always updates stats (even when paused). Pushes to `messageHistory`. If paused, enqueues to `pausedBuffer`; otherwise renders immediately if the message passes the active filter.
- `renderMessage(data)` — creates a `<div>` with theme badge, username, and text (with optional highlight span for filtered word).
- `togglePause()` / `setPauseButton()` — toggles `isPaused`, flushes buffer on resume.
- `toggleFilter(word)` / `clearFilter()` — sets `activeFilter`, triggers `rerenderChatLog()` which replays `messageHistory` through the filter.

#### `websocket.js`

- `openWS(channel)` — constructs WebSocket URL (`ws://` or `wss://` based on page protocol), attaches handlers.
- `setStatus(text, state)` — updates status text and dot color class.
- `onmessage` parses JSON and calls `ingestMessage(data)` for `type === 'message'` frames.

#### `background.js`

- `setBackground(url)` — sets `backgroundImage` on `#dynamic-background` and toggles the `.active` class (which drives the CSS opacity transition).
- `fetchBannerFromDecapi(channel)` — client-side fallback for reconnect (when `/api/start/` is not called).
- `loadProfile(channel)` — fetches avatar from `decapi.me/twitch/avatar/{channel}` and populates `#profile-bar`.

#### `llm-actions.js`

`runAction(url, opts)` is the shared handler for all three action buttons:
1. Opens the modal immediately with a spinner.
2. `POST`s to the endpoint.
3. Calls `opts.render(data)` to produce the modal body HTML.
4. Populates `#modal-meta` with note and message count.

Render functions: `renderTldr` (plain escaped text), `renderQuestions` (`<ul>` list), `renderVibe` (`<div class="vibe-badge">`).

#### `app.js`

Bootstrap file. Declares shared mutable state:

```javascript
const counts = { POSITIVE: 0, NEGATIVE: 0, NEUTRAL: 0 };
let total = 0, spamCount = 0;
let ws = null, currentChannel = '';
const messageHistory = [];
let activeFilter = null;
let isPaused = false;
const pausedBuffer = [];
```

On load:
1. Calls `applyTheme(getTheme())` — applies stored theme before any rendering.
2. Calls `initCharts()` — creates Chart.js instances.
3. Calls `setPauseButton()` — sets initial button label.
4. Wires all button event listeners.
5. If `INITIAL_CHANNEL` is set (ingestor already running), auto-reconnects the WebSocket and reloads the profile.

---

## 9. API Reference

### 9.1 HTTP Endpoints

All `POST` endpoints require the Django CSRF token, sent either as `X-CSRFToken` header or `csrfmiddlewaretoken` form field.

#### `POST /api/start/`

**Body:** `url=<twitch_url_or_channel_name>`

**Response 200:**
```json
{
  "channel": "channelname",
  "banner": "https://static-cdn.jtvnw.net/..." 
}
```
`banner` may be `null` if not found.

**Response 400:**
```json
{"error": "URL sau username Twitch invalid."}
```

#### `POST /api/stop/`

**Body:** (empty)

**Response 200:**
```json
{"stopped": true}
```

#### `GET /api/status/`

**Response 200:**
```json
{"channel": "channelname"}
```
`channel` is `null` if no ingestor is running.

#### `POST /api/summary/`

**Response 200:**
```json
{
  "summary": "The chat is discussing ...",
  "count": 87,
  "note": ""
}
```

#### `POST /api/extract-questions/`

**Response 200:**
```json
{
  "questions": ["What crosshair do you use?", "Is this ranked?"],
  "count": 87,
  "note": ""
}
```

#### `POST /api/vibe-check/`

**Response 200:**
```json
{
  "vibe": "Hype",
  "count": 87,
  "note": ""
}
```

### 9.2 WebSocket Protocol

**URL:** `ws://{host}/ws/chat/{channel}/`

Connection is unidirectional: the server sends JSON frames, the client never sends data. The connection is channel-scoped — only messages from the subscribed channel are received.

### 9.3 WebSocket Message Schema

Every frame is a JSON object. Always-present fields:

| Field | Type | Description |
|---|---|---|
| `type` | `"message"` | Discriminator (currently always `"message"`) |
| `username` | string | Twitch username |
| `text` | string | Chat message text |
| `channel` | string | Channel name |
| `sentiment` | `"POSITIVE"` \| `"NEGATIVE"` \| `"NEUTRAL"` | VADER classification |
| `score` | float | VADER compound score (−1.0 to 1.0) |
| `theme` | `"HYPE"` \| `"SPAM"` \| `"TECHNICAL"` \| `"CHAT"` | Theme classification |
| `velocity` | float | Messages/second (10-second rolling average) |
| `representative` | `{username, text}` \| null | Most expressive recent message (for chart tooltip) |

Conditionally-present fields:

| Field | Condition | Description |
|---|---|---|
| `top_words` | Every 8th message | `[[word, count], ...]` — top 10 words |
| `window_stats` | Every 5th message | `{unique_ratio, caps_ratio, mvps, window_size}` |

---

## 10. Data Schemas

### RabbitMQ Message (Ingestor → Worker)

```json
{
  "username": "streamer_fan42",
  "text": "Pog that was insane",
  "channel": "channelname"
}
```

JSON-encoded, published to `live_chat_queue`, `delivery_mode=2`.

### Redis Pub/Sub Message (Worker → Web)

Same as WebSocket Message Schema (section 9.3). Published to `chat_updates:{channel}`.

### Redis Buffer Entry (Worker → LLM Views)

```json
{"username": "streamer_fan42", "text": "Pog that was insane"}
```

Stored as JSON strings in list `chat_buffer:{channel}`. Maximum 100 entries.

---

## 11. Environment Variables

Defined in `.env` at the repository root. Loaded automatically by Docker Compose.

| Variable | Required | Description |
|---|---|---|
| `RABBITMQ_USER` | Yes | RabbitMQ username |
| `RABBITMQ_PASS` | Yes | RabbitMQ password |
| `DJANGO_SECRET_KEY` | Yes | Django secret key for CSRF/sessions |
| `DEBUG` | No | `True` or `False` (default `False`) |
| `GROQ_API_KEY` | No | Groq API key — LLM endpoints use heuristics without it |

Internal service-to-service addresses (`RABBITMQ_HOST`, `REDIS_HOST`) are set directly in `docker-compose.yml` environment sections using Docker's DNS service names.

---

## 12. Dependencies

### Web service (`backend/django_app/requirements.txt`)

| Package | Version | Role |
|---|---|---|
| `Django` | 4.2.13 | Web framework |
| `channels` | 4.1.0 | WebSocket support (ASGI) |
| `channels-redis` | 4.2.0 | Redis channel layer backend |
| `daphne` | 4.1.2 | ASGI server |
| `whitenoise` | 6.7.0 | Static file serving |
| `pika` | 1.3.2 | RabbitMQ AMQP client (ingestor) |
| `redis` | 5.0.7 | Redis client (sync for views, async for consumer) |
| `groq` | 0.9.0 | Groq LLM API client |
| `httpx` | 0.27.2 | Async-friendly HTTP client (banner fetch) |

### Worker service (`backend/worker/requirements.txt`)

| Package | Version | Role |
|---|---|---|
| `pika` | 1.3.2 | RabbitMQ AMQP client |
| `redis` | 5.0.7 | Redis client (pub/sub publish + LPUSH) |
| `vaderSentiment` | 3.3.2 | Rule-based sentiment analysis |

### Frontend (CDN)

| Library | Version | Role |
|---|---|---|
| `Chart.js` | 4.4.0 | Line chart (Hype Meter) + bar chart (Top Words) |
