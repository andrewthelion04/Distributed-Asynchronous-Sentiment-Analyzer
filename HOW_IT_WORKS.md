# How It Works — Application Flow & Technology Decisions

This document explains the application as a narrative: what happens at each step, why each technology was chosen, and how all the pieces connect. It is written for someone who already understands web development and Python but is encountering this system for the first time.

---

## The Core Problem

Twitch chat can move at hundreds of messages per second during popular streams. A naive approach — HTTP polling every few seconds — would miss most messages and add unnecessary server load. A direct browser-to-Twitch connection is impossible because Twitch IRC is a raw TCP protocol, not HTTP. The application therefore builds a pipeline:

```
Twitch IRC  →  Queue  →  Worker  →  Browser
(raw TCP)    (durable)  (analysis) (WebSocket)
```

Each arrow crosses a process or machine boundary. Each component in between is replaceable and independently scalable.

---

## Step 0: The User Pastes a URL

The user types a Twitch URL (or just a channel name) into the input field and clicks **Start**. `app.js` reads the value and makes a `POST /api/start/` request.

**Why POST and not GET?**
Starting a stream has a side effect: it launches two background threads. HTTP convention says GET must be idempotent (safe to repeat without consequences). POST is the correct verb for operations that change server state.

**The URL is normalised server-side, not client-side.**
`_extract_channel()` in `views.py` accepts both `https://www.twitch.tv/ninja` and `ninja` using two compiled regexes. Doing this in Python gives one canonical code path. The browser never has to parse Twitch URLs.

---

## Step 1: The Ingestor Starts

`views.start_stream` calls `manager.start(channel, ...)`. The `IngestorManager` is a **module-level singleton** — it lives in the Django process memory for the entire lifetime of the web container. This is a deliberate design choice: the ingestor does not need a database, a separate container, or any network discovery mechanism. It is simply a pair of Python daemon threads.

**Why threads, not asyncio?**
The Twitch IRC reader uses a blocking `socket` (not `asyncio`). The RabbitMQ publisher uses `pika.BlockingConnection`, which is also synchronous. Running them in threads lets each block independently without interfering with Daphne's asyncio event loop (which handles all WebSocket connections). The threads are `daemon=True`, so they die automatically when the process exits.

**Why two threads and not one?**
The responsibilities are separated by design:

- **`ingestor-twitch`** is I/O-bound: it blocks reading from the network, parses IRC lines, and enqueues payloads.
- **`ingestor-pika`** is also I/O-bound but against a different service (RabbitMQ). It needs to maintain heartbeats on its AMQP connection independently.

The `queue.Queue(maxsize=2000)` between them is a backpressure valve: if RabbitMQ is unreachable, the queue fills up, and subsequent `put_nowait()` calls silently drop messages rather than blocking the IRC reader. The IRC reader must never stall, because Twitch's IRC server sends a `PING` command periodically and closes the connection if it does not receive a `PONG` within a timeout window.

**Why not store the channel in a database?**
The active channel is a transient runtime concept, not persistent data. The Django process is the single source of truth. `manager.active_channel` is checked on every page load to auto-reconnect the WebSocket after a browser refresh.

---

## Step 2: Twitch IRC Protocol

The `_twitch_reader` thread opens a raw TCP socket to `irc.chat.twitch.tv:6667` and sends three lines:

```
PASS SCHMOOZE
NICK justinfan<random-number>
JOIN #channel
```

Twitch accepts any `justinfan*` nickname as an anonymous read-only connection. No OAuth token is needed. The password field is ignored by the server. This is Twitch's officially documented anonymous access method.

The reader then enters a read loop. IRC messages are newline-delimited strings. The code accumulates incoming bytes into a string buffer and splits on `\r\n` rather than assuming each `recv()` call delivers exactly one complete message — because TCP is a stream protocol, not a message protocol.

**Handling PING:**
Every few minutes, Twitch sends `PING :tmi.twitch.tv`. Failing to respond causes a disconnect. The reader checks `if line.startswith('PING')` and immediately writes `PONG + line[4:] + '\r\n'` back.

**Parsing PRIVMSG:**
Chat messages arrive as:
```
:username!username@username.tmi.twitch.tv PRIVMSG #channel :message text here
```

The compiled regex `_PRIVMSG_RE` extracts `username`, `channel`, and `text` in one operation. Non-PRIVMSG lines (JOIN, PART, NOTICE, etc.) are silently ignored.

---

## Step 3: RabbitMQ as the Work Queue

Each parsed message is enqueued locally (`queue.Queue`), then the `ingestor-pika` thread picks it up and publishes to RabbitMQ:

```python
ch.basic_publish(
    exchange='',
    routing_key='live_chat_queue',
    body=json.dumps(payload),
    properties=pika.BasicProperties(delivery_mode=2),
)
```

**Why RabbitMQ instead of publishing directly to Redis?**

RabbitMQ adds durability and backpressure that Redis pub/sub alone cannot provide:

- **Durability:** `delivery_mode=2` persists messages to disk. If the worker crashes, messages accumulate in the queue and are processed when it restarts. Redis pub/sub is fire-and-forget — if no subscriber is listening, the message is gone.
- **Backpressure:** `prefetch_count` limits how many messages the worker takes at once, matching its actual processing capacity. If the worker is slow, RabbitMQ holds the excess. If the ingestor is fast, messages queue up safely.
- **Decoupling:** The ingestor has no knowledge of the worker. They communicate only through a named queue. You could add more workers (multiple `docker compose up --scale worker=3`) without changing a line of code.

**Why not Kafka?**
Kafka is designed for very high-throughput log storage with retention and replay. For this use case — transient chat analysis where old messages are irrelevant once processed — RabbitMQ's simpler queue semantics are a better fit and much easier to operate.

---

## Step 4: The Worker Analyses Each Message

The worker is a standalone Python process, completely separate from Django. It runs in its own Docker container with its own dependencies.

**Why a separate process/container?**

`vaderSentiment` loads a ~500KB lexicon into memory at startup and then runs pure CPU computation. Isolating it means:

1. The Django web server is never delayed by CPU-heavy analysis.
2. The worker can be scaled independently (`--scale worker=3`).
3. If analysis crashes (e.g., malformed input), it does not take down the web server.

**Why vaderSentiment?**

VADER (Valence Aware Dictionary and sEntiment Reasoner) is chosen over alternatives for several reasons:

- **No GPU required.** Transformer models (BERT, RoBERTa) give better accuracy but require gigabytes of RAM and a GPU for real-time performance. VADER runs in microseconds on any CPU.
- **Social media calibration.** VADER is trained on tweets and product reviews — colloquial, emoji-heavy, ALL CAPS text. This is similar to Twitch chat.
- **Extensible lexicon.** The `analyzer.lexicon.update(TWITCH_LEXICON)` call injects custom gaming vocabulary. No retraining needed.
- **Compound score.** VADER returns a single normalized score from −1.0 to 1.0, making thresholding trivial.

The custom `TWITCH_LEXICON` is the critical extension. Without it, `Pog`, `monkaS`, and `LUL` all score 0.0 (neutral). With it, they carry meaningful valence.

**ThreadPoolExecutor:**

The RabbitMQ I/O loop runs on one thread (pika's requirement). Analysis is submitted to a `ThreadPoolExecutor(max_workers=4)`. This means up to 4 messages can be analysed in parallel:

```
pika I/O thread
  │
  ├── [on_message] executor.submit(_process_and_publish)
  │       worker thread 1: analysing "Pog that's insane"
  │       worker thread 2: analysing "monkaS is he ok"
  │       worker thread 3: analysing "gg ez"
  │       worker thread 4: analysing "!commands"
  │
  └── [on_done callback] channel.basic_ack(...)
```

The `add_callback_threadsafe` call is essential: `basic_ack` must be called from pika's I/O thread, not from the executor thread. `connection.add_callback_threadsafe` schedules the ack to run on the next pika event loop iteration.

**Shared in-memory state and thread safety:**

`_word_counter`, `_ts_deque`, and `_recent_msgs` are shared across all worker threads. Every read and write to these structures is wrapped in `with _lock:`. The lock is coarse-grained (one lock for all state) but this is acceptable — contention is minimal because each operation is short (a few Counter updates, a deque append).

---

## Step 5: Two Redis Operations per Message

After analysis, the worker does two things with Redis:

### 5a. Circular Buffer (LLM support)

```python
redis.lpush(f'chat_buffer:{channel}', json.dumps({'username': ..., 'text': ...}))
redis.ltrim(f'chat_buffer:{channel}', 0, 99)
```

`LPUSH` prepends the new message to the list head (newest-first order). `LTRIM` immediately truncates the list to 100 items. This is a constant-time sliding-window operation — no matter how many messages arrive, the list never grows beyond 100 entries.

When the user clicks TL;DR or Vibe Check, the Django view calls `LRANGE chat_buffer:channel 0 99` to retrieve these 100 most recent messages and sends them to the Groq API.

**Why Redis for this and not a database?**
Speed and simplicity. This buffer is write-intensive (every message), read-rarely (only on LLM button click), and entirely disposable (no historical value). Redis handles this pattern in microseconds. A relational database would add schema overhead and disk writes for no benefit.

### 5b. Pub/Sub Publish (Real-Time Updates)

```python
redis.publish(f'chat_updates:{channel}', json.dumps(result))
```

This is a fire-and-forget publish. Any subscriber currently listening on `chat_updates:{channel}` receives the message immediately. There is no queue, no persistence, no retry. If no subscriber is listening (e.g., no browser tab is open), the message is silently discarded. This is correct behaviour — the dashboard is real-time only, not a historical record.

**Why not use RabbitMQ for this as well?**
RabbitMQ's strength is durable queuing. For live dashboard updates, durability is counterproductive — by the time a reconnecting browser receives a queued-up burst of 10,000 old messages, they are already stale. Redis pub/sub's ephemeral nature is exactly right here.

---

## Step 6: Django Channels Bridges Redis to WebSocket

The `ChatConsumer` is an `AsyncWebsocketConsumer` — it runs on Daphne's asyncio event loop without blocking.

When a browser opens `ws://localhost:8000/ws/chat/ninja/`:

1. Daphne accepts the WebSocket handshake.
2. Django Channels routes the connection to `ChatConsumer` based on `ws/chat/<str:channel>/`.
3. `connect()` accepts the connection, opens an async Redis connection, subscribes to `chat_updates:ninja`, and spawns `_listen()` as an asyncio background task.

`_listen()` is a simple async for loop:

```python
async for message in self._pubsub.listen():
    if message['type'] == 'message':
        await self.send(message['data'].decode('utf-8'))
```

Every time the worker publishes to `chat_updates:ninja`, this loop wakes up and forwards the raw JSON string to the browser. No transformation, no parsing — the exact bytes the worker serialised are sent to the browser.

**Why not use Django Channels groups?**
Django Channels groups are built on top of the channel layer (also Redis). The consumer would `group_add` on connect, and the worker would call `group_send`. This is more idiomatic for Channels but adds an extra layer of indirection and serialisation. Direct pub/sub is simpler and equivalent in this architecture because the isolation unit is already `chat_updates:{channel}` — which is exactly what a Channels group would implement.

**Why Daphne instead of Gunicorn?**
Gunicorn is a WSGI server. WSGI is synchronous and connection-per-thread — it cannot handle long-lived WebSocket connections without consuming one thread per connection indefinitely. Daphne is an ASGI server that handles thousands of concurrent WebSocket connections on a small asyncio event loop. The same Daphne process handles all HTTP requests AND all WebSocket connections simultaneously.

---

## Step 7: The Browser Receives a Message

The WebSocket `onmessage` handler in `websocket.js` parses the JSON and calls `ingestMessage(data)` in `chat.js`. This single function is the junction point for all UI updates:

```javascript
function ingestMessage(data) {
    // Always update: stats, velocity display, top words, window stats
    updateSentimentCounters(data.sentiment);
    lastVelocity = data.velocity;
    if (data.top_words)    updateTopWords(data.top_words);
    if (data.window_stats) updateWindowStats(data.window_stats);

    // History for cross-filter replay
    messageHistory.push(data);

    // Conditional rendering
    if (isPaused) {
        pausedBuffer.push(data);
    } else if (matchesFilter(data)) {
        renderMessage(data);
    }
}
```

**Why are stats updated even when paused?**
The pause feature is about reading comfort, not about stopping data collection. If you pause the chat log to read something, the Hype Meter should still reflect what is happening in the stream. Only the visual scrolling stops.

**Why keep `messageHistory`?**
When the user clicks a word in the Top Words bar chart, `toggleFilter(word)` sets `activeFilter` and calls `rerenderChatLog()`, which replays all of `messageHistory` through `matchesFilter()`. Without history, you would only see future messages matching the filter. With history (up to 400 messages), the log immediately populates with past relevant messages.

---

## Step 8: The Hype Meter Updates Every Second

`charts.js` runs a `setInterval` every 1000ms regardless of incoming messages:

```javascript
setInterval(() => {
    hypeData.shift();     hypeData.push(lastVelocity);
    hypeMessages.shift(); hypeMessages.push(lastRepresentative);
    hypeLabels.shift();   hypeLabels.push('');
    hypeChart.update('none');
    lastRepresentative = null;
}, 1000);
```

`lastVelocity` is the last `velocity` value received from any WebSocket message within this second. `lastRepresentative` is the last `representative` object received. The `'none'` argument to `chart.update()` skips animation — at 1 Hz, animation would make the chart feel laggy.

**Why not push a new chart point on every message?**
At high-volume streams (50+ msg/sec), calling `chart.update()` 50 times per second would saturate the browser's rendering. A 1-second tick rate is the natural granularity for a "messages per second" metric.

**The representative message for the tooltip:**
The worker's `_get_representative()` function selects the most expressive non-spam message from the last 2 seconds. This is attached to the chart point and shown in the Chart.js tooltip when the user hovers over a node. The browser does not compute this — the server does, because it has access to all concurrent messages, not just the one that triggered the current WebSocket frame.

---

## Step 9: The LLM Action Bar

When the user clicks **Generează TL;DR**, `runAction()` fires a `POST /api/summary/` request. The server:

1. Reads `chat_buffer:{channel}` from Redis (100 most recent messages).
2. Formats them as `username: text` lines.
3. Sends them to Groq (Llama 3.1 8B Instant) with a focused prompt.
4. Returns the response.

**Why Groq and not OpenAI?**
Groq's inference hardware (LPUs) gives much lower latency than OpenAI's API for small models. `llama-3.1-8b-instant` responds in under a second in most cases. For real-time chat analysis where the user is waiting for a result, latency matters more than raw quality.

**Why a local fallback?**
The Groq API key is optional. The system works without it, degrading gracefully to heuristics. `_local_summary()` returns the top-5 most frequent non-stop words as a topic hint. `_local_vibe()` applies ratio-based rules (caps ratio, laugh ratio, hype emote ratio). This makes the application usable for development and demonstration without external API dependencies.

**Why are there three separate LLM endpoints instead of one?**
Each task needs a fundamentally different prompt and response format:
- Summary → 1-2 sentences of free text
- Questions → a list of extracted items
- Vibe → a single short phrase

A single endpoint with a `mode` parameter would work but would complicate both the server logic and the frontend rendering. Three endpoints with three render functions (`renderTldr`, `renderQuestions`, `renderVibe`) are clearer and independently modifiable.

---

## Step 10: Dark Mode and Theme Persistence

The theme system is implemented entirely with CSS custom properties:

```css
:root {
  --bg: #0e0e10;
  --accent: #9146ff;  /* Twitch purple */
  ...
}
:root[data-theme="light"] {
  --bg: #f5f5f7;
  --accent: #9146ff;  /* same accent, adjusted hover */
  ...
}
```

Toggling theme is one line:
```javascript
document.documentElement.setAttribute('data-theme', 'light');
```

No class manipulation, no style injection, no JavaScript color constants. The browser's CSS engine handles all re-rendering automatically. The only JavaScript needed is re-tinting Chart.js, which stores colors in its own JavaScript objects and does not read CSS variables dynamically.

The preference is stored in `localStorage`, so it persists across page refreshes and browser restarts without any server involvement.

---

## The Dynamic Background

When `/api/start/` returns, the `banner` URL (if present) is passed to `setBackground()`:

```javascript
function setBackground(url) {
    const bg = document.getElementById('dynamic-background');
    bg.style.backgroundImage = `url("${url}")`;
    bg.classList.add('active');
}
```

The `#dynamic-background` element is `position: fixed; z-index: -1; filter: blur(15px) brightness(0.2)`. The blur and darkening make the image serve as a subtle atmospheric layer without obscuring the UI. The `.active` class triggers an `opacity: 0 → 1` CSS transition over 0.6 seconds, avoiding a jarring instant flash.

The server fetches the banner URL from `decapi.me` (a Twitch data proxy) using two fallback endpoints: the offline image first (high-resolution scene), then the profile banner. The 2.5-second `httpx` timeout prevents the `/api/start/` response from hanging if decapi.me is slow.

---

## Architecture Trade-offs and Limitations

| Decision | Trade-off |
|---|---|
| Single ingestor per Django process | Simple, but limits to one active channel at a time. Multiple channels would require multiple `IngestorManager` instances or a separate ingestor service. |
| In-memory worker state | Fast, but resets on worker restart. Word counts and window stats start from zero after a container restart. Acceptable for a real-time dashboard. |
| One global lock in the worker | Avoids deadlocks, easy to reason about. Could become a bottleneck at very high message rates (1000+ msg/sec) with many worker threads. |
| VADER over transformer models | Sub-millisecond inference, no GPU. Lower accuracy on ambiguous messages. The custom Twitch lexicon partially compensates. |
| `justinfan` anonymous access | Read-only. Cannot access subscriber-only chats. No rate limiting above Twitch's anonymous IRC limits. |
| Redis pub/sub for real-time | No message history for late-joining WebSocket clients. A browser that connects after messages were published misses them (which is fine — the dashboard is live-only). |
| Groq Llama 3.1 8B | Faster and cheaper than larger models. May produce lower-quality summaries for complex multilingual chats. |

---

## Technology Choice Summary

| Technology | Why It's Here |
|---|---|
| **Django** | Solid, batteries-included framework. Template rendering + ORM (future use) + auth middleware (future use). ASGI support via Channels. |
| **Daphne** | ASGI server that handles both HTTP and WebSocket natively. Required by Django Channels. |
| **Django Channels** | Adds WebSocket support to Django without blocking the event loop. |
| **RabbitMQ** | Durable message queue with backpressure control. Decouples ingestor from worker. Survives worker crashes. |
| **Redis** | Two roles: fast pub/sub for real-time push, and LPUSH/LTRIM sliding window for LLM context. |
| **vaderSentiment** | No-GPU, sub-millisecond sentiment analysis calibrated for social media text. Extensible via custom lexicon. |
| **Groq / Llama 3.1** | Low-latency LLM inference for on-demand chat analysis. |
| **Chart.js** | Zero-dependency chart library with good interactivity (hover, click). No build step. |
| **Docker Compose** | One-command startup of four interdependent services with health checks and named networking. |
| **decapi.me** | Public Twitch data proxy (no OAuth) for fetching avatars and banner images. |
