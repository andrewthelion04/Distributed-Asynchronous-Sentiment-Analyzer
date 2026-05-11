# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture

Four Docker services orchestrated by `docker-compose.yml`, communicating over a shared virtual network:

```
Browser ──WebSocket──▶ Django ASGI (web:8000) ◀── Redis pub/sub ◀── Worker
                              │
                              │ POST /api/start/
                              ▼
                         IngestorManager
                              │ (threads)
                              ▼
                     Twitch IRC (irc.chat.twitch.tv:6667)
                              │
                              ▼
                       RabbitMQ (live_chat_queue)
                              │
                              ▼
                    Worker ──▶ vaderSentiment ──▶ Redis publish
```

**`web_app/`** — Django 4.2 ASGI app served by **Daphne**. Handles HTTP + WebSockets via Django Channels. Manages the Twitch IRC ingestor as background threads.

**`worker/`** — Standalone Python service. Consumes `live_chat_queue` from RabbitMQ, classifies with **vaderSentiment** (ThreadPoolExecutor), publishes JSON results to Redis channel `chat_updates`.

**`rabbitmq`** — `rabbitmq:3.12-management`. Queue: `live_chat_queue`, durable. Management UI on port 15672.

**`redis`** — `redis:7-alpine`. Dual role:
1. Django Channels channel layer (internal channels infrastructure)
2. Pub/sub bus (`chat_updates` channel) — worker publishes, Daphne consumers subscribe

## Data flow

1. User pastes Twitch URL → `POST /api/start/` → `views.start_stream` extracts channel name via regex.
2. `IngestorManager.start()` launches two daemon threads:
   - **twitch-reader**: connects anonymously to `irc.chat.twitch.tv:6667` (nick `justinfanXXXXX`, no OAuth), joins `#channel`, reads PRIVMSG lines, handles PING→PONG.
   - **ingestor-pika**: maintains persistent pika connection; drains the internal `queue.Queue` and publishes JSON `{username, text, channel}` to RabbitMQ.
3. Worker consumes from RabbitMQ → vaderSentiment compound score → classifies POSITIVE/NEGATIVE/NEUTRAL → `redis.publish('chat_updates', json)`.
4. Django Channels `ChatConsumer` (AsyncWebsocketConsumer) subscribes to `chat_updates` via `redis.asyncio` pub/sub and forwards each message to the browser WebSocket.
5. Frontend JS updates counters and scrolling chat log in real-time without page refresh.

## Key files

| File | Role |
|------|------|
| `web_app/analyzer/ingestor.py` | `IngestorManager` — manages Twitch IRC + pika threads; global `manager` singleton |
| `web_app/analyzer/consumers.py` | `ChatConsumer` — AsyncWebsocketConsumer, subscribes to Redis pub/sub |
| `web_app/analyzer/routing.py` | WebSocket URL routing (`ws/chat/`) |
| `web_app/analyzer/views.py` | `start_stream` (starts ingestor), `stop_stream`, `stream_status` |
| `web_app/sentiment_project/asgi.py` | ASGI entry point — ProtocolTypeRouter for HTTP + WebSocket |
| `web_app/sentiment_project/settings.py` | Django settings, CHANNEL_LAYERS (Redis), RABBITMQ_* |
| `worker/worker.py` | RabbitMQ consumer + vaderSentiment + Redis publish |
| `docker-compose.yml` | Service definitions: rabbitmq, redis, web, worker |
| `.env` | `RABBITMQ_USER/PASS`, `DJANGO_SECRET_KEY`, `DEBUG` — never commit |

## Development commands

```bash
# Pornire completa (prima data sau dupa modificari Dockerfile/requirements)
docker compose up --build

# Pornire fara rebuild
docker compose up

# Resetare completa (sterge DB si cache)
docker compose down -v

# Loguri live
docker compose logs -f web
docker compose logs -f worker

# Shell in containerul Django
docker compose exec web bash
```

## RabbitMQ Management UI

Accesibil la `http://localhost:15672` cu credentialele din `.env`.
Sectiunea **Queues → live_chat_queue** arata mesajele in asteptare.

## Twitch anonymous access

Ingestorul se conecteaza fara OAuth folosind nickname-ul conventional `justinfanNNNNN` cu parola ignorata (`SCHMOOZE`). Ofera acces read-only la orice canal public. Nu necesara niciun token Twitch.

## Sentiment classification (vaderSentiment)

| compound score | Sentiment |
|---|---|
| >= 0.05 | POSITIVE |
| <= -0.05 | NEGATIVE |
| between | NEUTRAL |

VADER este calibrat pe social media english — performanta buna pe chat Twitch (emoticoane, slang, ALL CAPS). Nu necesita GPU.


# MEGA-PROMPT EXHAUSTIV: Live Twitch Analytics Dashboard & Context Snapshot

Ești un Senior Full-Stack Developer și Arhitect de Sisteme. Sarcina ta este să generezi codul COMPLET și FUNCȚIONAL (fără mock-uri simpliste) pentru aplicația "Distributed-Asynchronous-Sentiment-Analyzer", transformată acum într-un instrument avansat de monitorizare live a chat-urilor de Twitch.

Vrem un sistem real-time care se conectează la Twitch via IRC, procesează datele asincron prin RabbitMQ + un Worker Python (multiprocessing), și trimite rezultatele instantaneu către o interfață web Django folosind WebSockets. În plus, sistemul va oferi un rezumat la cerere (TL;DR) al chat-ului folosind un LLM.

Aplicația trebuie să fie gata de rulare (prin Docker Compose).

---

### ARHITECTURA ȘI CERINȚE STRICTE

Vom avea 4 servicii în `docker-compose.yml`:
1. **web:** Django cu Django Channels (ASGI).
2. **redis:** Cu rol dublu: Message Broker pentru WebSockets (channel layer) și Circular Buffer pentru mesajele recente.
3. **rabbitmq:** Broker-ul AMQP principal pentru preluarea volumului mare de date live.
4. **worker:** Scriptul independent de analiză a datelor.
*(Notă: Ingestorul de Twitch va fi lansat/gestionat de backend pe baza URL-ului furnizat de utilizator).*

---

### SARCINILE DE IMPLEMENTARE DETALIATE (SCRIE CODUL COMPLET PENTRU URMĂTORII PAȘI):

#### PASUL 1: Docker și Infrastructura
- Scrie `docker-compose.yml` incluzând `web`, `redis`, `rabbitmq`, `ingestor` și `worker`.
- Creează `requirements.txt` cu: `Django`, `channels`, `channels_redis`, `pika`, `redis`, `openai` (pentru feature-ul TL;DR).

#### PASUL 2: Backend-ul Django (Parsare URL, WebSockets & API)
- Scrie `views.py`. Când utilizatorul introduce un link (ex: `https://www.twitch.tv/ninja`), folosește o funcție pentru a extrage exact username-ul (`ninja`). Backend-ul declanșează Ingestorul pentru acest canal.
- Creează un API endpoint `/api/generate-summary/`. La apelare, extrage ultimele 100 de mesaje din Redis (vezi Pasul 4) și apelează API-ul OpenAI (sau oferă o structură clară pentru el) cu promptul: *"Rezumă în 1-2 propoziții care este subiectul principal de discuție. Ignoră spam-ul."* Returnează textul.
- Configurează `asgi.py` și routing-ul pentru WebSockets.
- Scrie `consumers.py`: Creează un `AsyncWebsocketConsumer`. Folosește numele canalului ca `room_group_name` (WebSocket Rooms) pentru a izola datele stream-urilor.

#### PASUL 3: Conexiunea Reală la Twitch (Ingestor - PRODUCER)
- Creează `ingestor.py`. Scrie un script care se conectează real la `irc.chat.twitch.tv` (port 6667). 
- Gestionează comanda "PING" de la Twitch cu "PONG".
- Extrage username-ul și textul (`PRIVMSG`), și publică fiecare mesaj extras (format JSON) în RabbitMQ (`live_chat_queue`).

#### PASUL 4: Worker-ul Asincron (CONSUMER, METRICI & REDIS BUFFER)
- Creează `worker.py` care ascultă `live_chat_queue` din RabbitMQ. Folosește `concurrent.futures.ThreadPoolExecutor`.
- **Logica de Analiză (Metrici Noi):**
  1. *Temă/Spam:* Clasifică simplu mesajul (ex: *Hype* dacă are CAPS LOCK, *Spam* dacă e repetitiv, *Tehnic/Gameplay* pe baza unor cuvinte cheie).
  2. *Top Cuvinte:* ține evidența celor mai folosite cuvinte (fără stop-words).
  3. *Chat Velocity (Hype Meter):* Calculează mesaje/secundă.
- **Acțiunea 1 (WebSockets):** Publică metricile calculate și mesajul procesat în canalul Redis ascultat de Django Channels pentru a fi trimise live pe ecran.
- **Acțiunea 2 (Circular Buffer pentru TL;DR):** Adaugă mesajul într-o listă Redis dedicată canalului respectiv. Folosește comenzile `LPUSH` și `LTRIM` pentru a păstra strict o "fereastră alunecătoare" a ultimelor 100 de mesaje.

#### PASUL 5: Frontend-ul (UI/UX, Charts & TL;DR)
- Scrie `index.html`. Include un input pentru "Twitch URL".
- **UX Profil:** La schimbarea link-ului, afișează numele noului streamer și poza lui de profil (folosește un request către `https://decapi.me/twitch/avatar/{username}`).
- **Reset State:** Scrie funcția JS `resetDashboard()` care se apelează la un link nou, curățând logul de chat și resetând graficele.
- **WebSockets & Charts:** Conectează-te la `ws://<host>/ws/chat/<room>/`. La primirea datelor, actualizează:
  1. *Hype Meter (Line Chart)* - Evoluția numărului de mesaje/secundă.
  2. *Top Cuvinte (Bar Chart sau Listă)* - Cele mai frecvente cuvinte.
  3. *Live Chat Log* - O zonă de scroll pentru mesaje, colorate în funcție de clasificare (Hype/Spam/etc).
- **Feature TL;DR:** Adaugă un buton "Generează Context (TL;DR)". La apăsare, afișează un spinner, fă fetch către `/api/generate-summary/` și afișează rezumatul generat într-un modal/card.

#### PASUL 6: Documentația Finală a Sistemului
- Generează fișierul `README.md` care explică cum comunică părțile, rolul buffer-ului Redis (LPUSH/LTRIM) pentru LLM și WebSockets.

Asigură-te că oferi structura exactă de foldere și fișiere și explică exhaustiv fluxul datelor.