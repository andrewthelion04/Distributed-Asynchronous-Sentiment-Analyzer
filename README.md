# Distributed Async Twitch Sentiment & Analytics

Sistem real-time, containerizat și distribuit pentru monitorizarea chat-urilor de Twitch. Captează mesajele live prin IRC, le procesează asincron printr-un worker Python (vaderSentiment + lexicon Twitch + metrici euristice) și le afișează într-un dashboard web Django Channels cu grafice interactive, analiză on-demand cu LLM și fundal imersiv din banner-ul streamer-ului.

## Arhitectură

```
Browser ──WebSocket──▶ Django ASGI (Daphne, web:8000) ◀── Redis pub/sub ◀── Worker
                              │
                              │ POST /api/start/
                              ▼
                         IngestorManager
                              │ (threads)
                              ▼
                     Twitch IRC (irc.chat.twitch.tv:6667)
                              │
                              ▼
                       RabbitMQ (live_chat_queue, durable)
                              │
                              ▼
                    Worker ──▶ VADER (+ Twitch lexicon) ──▶ Redis publish
```

Patru servicii orchestrate de `docker-compose.yml`, conectate pe o reţea internă:

| Serviciu | Imagine | Rol |
|---|---|---|
| **web** | `python:3.11-slim` (custom) | Django 4.2 + Channels rulând pe Daphne; gestionează HTTP, WebSocket-uri, IngestorManager-ul Twitch IRC |
| **worker** | `python:3.11-slim` (custom) | Consumator RabbitMQ; clasifică sentimentul cu VADER + calculează metrici live |
| **rabbitmq** | `rabbitmq:3.12-management` | Broker AMQP pentru `live_chat_queue` (UI pe `:15672`) |
| **redis** | `redis:7-alpine` | Dublu rol — channel layer Django Channels + pub/sub bus worker→web + buffer circular pentru LLM |

## Fluxul de date

1. Browser-ul trimite URL Twitch → `POST /api/start/` extrage numele canalului via regex.
2. Server-ul fetch-uieşte banner-ul streamerului prin `decapi.me` (offline image cu fallback la profile banner) şi îl returnează frontend-ului împreună cu numele canalului.
3. `IngestorManager.start()` porneşte două thread-uri demon:
   - **twitch-reader** — se conectează anonim la `irc.chat.twitch.tv:6667` (nickname `justinfanNNNNN`, PASS `SCHMOOZE`, fără OAuth), face JOIN pe `#channel`, citeşte `PRIVMSG`-uri, răspunde la `PING`.
   - **ingestor-pika** — drenează coada internă şi publică JSON-uri `{username, text, channel}` în RabbitMQ.
4. Worker-ul consumă din `live_chat_queue` cu `ThreadPoolExecutor` şi pentru fiecare mesaj:
   - aplică VADER (cu lexiconul Twitch injectat) → POSITIVE/NEGATIVE/NEUTRAL,
   - clasifică tematic mesajul (HYPE / SPAM / TECHNICAL / CHAT),
   - actualizează contoarele de cuvinte, viteza şi statisticile de fereastră,
   - face `LPUSH` + `LTRIM 0 99` într-o listă Redis dedicată canalului (`chat_buffer:{channel}`),
   - publică rezultatul pe `chat_updates:{channel}` (Redis pub/sub).
5. `ChatConsumer` (AsyncWebsocketConsumer) subscrie la pub/sub via `redis.asyncio` şi forward-ează fiecare mesaj browser-ului.
6. Front-end-ul actualizează contoarele, graficele Chart.js, logul de chat, panoul de statistici şi fundalul fără refresh.

## Sentiment cu lexicon Twitch

VADER tratează emote-urile şi slang-ul de Twitch ca scor 0. Worker-ul injectează un `TWITCH_LEXICON` (cca. 60 termeni — `Pog`, `KEKW`, `OMEGALUL`, `monkaS`, `Sadge`, `trash`, `tilted`, etc.) cu valenţe între -3.5 şi +3.5 înainte de procesare:

```python
_analyzer = SentimentIntensityAnalyzer()
_analyzer.lexicon.update(TWITCH_LEXICON)
```

Cheile sunt lowercase — VADER face lookup pe `item.lower()`, deci `Pog`, `POG`, `pog` se potrivesc uniform, iar varianta ALL CAPS într-o propoziţie mixtă primeşte automat boost-ul de intensificator (C_INCR = +0.733).

Pragurile finale rămân pe scorul `compound`:

| compound | Sentiment |
|---|---|
| ≥ +0.05 | POSITIVE |
| ≤ −0.05 | NEGATIVE |
| între | NEUTRAL |

## Metrici live (heuristice, fără AI)

Calculate de worker pe fereastra ultimelor ~100 mesaje, trimise prin WebSocket la fiecare 5 mesaje procesate:

- **Hype Meter** — mesaje pe secundă, fereastră alunecătoare de 10s.
- **Top Cuvinte** — bar chart click-abil pentru filtrare cross-chart a chat-ului.
- **Audienţă Organică (Unique Chatter Ratio)** — `(useri unici / mesaje) × 100`. Sub 30% = potenţial copy-pasta / spam wave; peste 60% = audienţă diversificată.
- **Caps-Lock Ratio** — `(majuscule / litere alfa) × 100`. Peste 40% = hype/rage.
- **Top 3 MVPs** — leaderboard al utilizatorilor cu cele mai multe mesaje în fereastră.
- **Tematică** per mesaj — HYPE (>60% caps), SPAM (mesaj duplicat de la acelaşi user), TECHNICAL (cuvinte cheie gaming), CHAT (default).

## Analiză LLM on-demand (Action Bar)

Trei endpoint-uri Django alimentate de buffer-ul circular Redis (`LPUSH`/`LTRIM` la ultimele 100 mesaje per canal):

| Endpoint | Buton | Prompt LLM | Răspuns |
|---|---|---|---|
| `POST /api/summary/` | TL;DR | "Summarize in 1-2 sentences what the main topic is..." | paragraf |
| `POST /api/extract-questions/` | Extrage Întrebări | "Extract only clear and relevant questions addressed to the streamer..." | `<ul>` |
| `POST /api/vibe-check/` | Vibe Check | "Respond with a single short phrase describing the general vibe..." | badge |

Toate folosesc Groq cu modelul `llama-3.1-8b-instant` când `GROQ_API_KEY` e setat în `.env`. Când lipseşte cheia, fiecare endpoint cade pe o euristică locală:
- Summary → top cuvinte ne-stop-words.
- Questions → grep pe `?` cu deduplicare.
- Vibe → scoring pe caps ratio, laughter tokens, hype emotes, cuvinte negative.

## Dashboard UI

- **Action Bar** central — 3 butoane cu gradient distinct (mov / albastru / verde) pentru cele trei acţiuni LLM.
- **Hype Meter** (line chart) cu tooltip personalizat afişând mesajul reprezentativ din secunda respectivă.
- **Top Cuvinte** (bar chart) click-abil — click pe un cuvânt filtrează live chat-ul (cross-filter), click pe acelaşi cuvânt anulează filtrul; cuvântul filtrat este evidenţiat în mesajele rămase.
- **Chat Live** într-un panou îngust (≈25% din lăţime) cu buton **Pauză / Reia** — pauza buffer-ează mesajele într-un array JS şi le afişează în bloc la reluare, fără a pierde nimic.
- **Audienţă & Velocitate** — panou nou cu bare verzi/galbene/roşii pentru unique ratio şi caps ratio, plus lista Top 3 MVPs.
- **Sentiment Panel** lateral retrogradat — mini-carduri compacte cu procentele POS/NEG/NEU.
- **Dark / Light Theme** comutabil din header (persistă în `localStorage`); variabile CSS pe `:root` pentru sincronizare instant a graficelor.
- **Fundal imersiv** — banner-ul streamerului aplicat pe `#dynamic-background` cu `filter: blur(15px) brightness(0.2)` pentru a păstra lizibilitatea textului peste imagine.

## Comenzi dev

```bash
# Pornire completă (prima dată sau după modificări de Dockerfile / requirements)
docker compose up --build

# Pornire fără rebuild
docker compose up

# Reset complet (şterge volumele Redis & RabbitMQ)
docker compose down -v

# Loguri live
docker compose logs -f web
docker compose logs -f worker

# Shell în containerul Django
docker compose exec web bash
```

UI principal: <http://localhost:8000>. Management RabbitMQ: <http://localhost:15672> (credenţialele sunt din `.env`).

## Variabile de mediu (`.env`)

| Cheie | Rol |
|---|---|
| `RABBITMQ_USER` / `RABBITMQ_PASS` | credentials AMQP (replicate şi în compose) |
| `DJANGO_SECRET_KEY` | Django settings |
| `DEBUG` | `True` în dev |
| `GROQ_API_KEY` | (opţional) — dacă lipseşte, action bar-ul cade pe euristici locale |

## Acces anonim Twitch

Conexiunea IRC nu necesită OAuth: nickname-ul convenţional `justinfanNNNNN` cu parola `SCHMOOZE` oferă acces read-only la orice canal public. Niciun token Twitch nu este stocat sau cerut.

## Structura proiectului

```
.
├── docker-compose.yml
├── .env
├── web_app/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── manage.py
│   ├── sentiment_project/        # settings.py, asgi.py, urls.py
│   └── analyzer/
│       ├── ingestor.py           # IngestorManager — threads Twitch IRC + pika
│       ├── consumers.py          # ChatConsumer — WebSocket + Redis pub/sub
│       ├── routing.py            # ws/chat/<channel>/
│       ├── views.py              # /api/start, /stop, /summary, /extract-questions, /vibe-check
│       ├── urls.py
│       └── templates/analyzer/index.html
└── worker/
    ├── Dockerfile
    ├── requirements.txt
    └── worker.py                 # VADER + Twitch lexicon + metrici + Redis publish
```
