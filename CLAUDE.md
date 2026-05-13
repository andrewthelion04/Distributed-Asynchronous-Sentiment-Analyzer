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


# PROMPT: Faza 4 - Rafinare UI/UX și Interactivitate Avansată

Ești un Senior Full-Stack Developer. Aplicația noastră "Twitch Live Analytics Dashboard" este funcțională, iar datele curg perfect în timp real. Acum trebuie să facem un *polish* (retuș) la nivel de Frontend (HTML/CSS/JS) pentru a oferi o experiență premium și interactivă, specifică platformelor de gaming.

Te rog să actualizezi fișierele de frontend (`index.html`, stilurile CSS și logica JS) pentru a implementa următoarele 5 funcționalități. Nu este nevoie să modifici backend-ul (Django/RabbitMQ/Worker) decât dacă este absolut necesar pentru trimiterea datelor.

### 1. REORGANIZARE LAYOUT (Focus pe Context & Hype)
- **Centrarea Butonului TL;DR:** Mută butonul de "Generează Context (TL;DR)" într-o poziție centrală, proeminentă (de exemplu, deasupra zonei de afișare a chat-ului), făcându-l un element principal de tip *Call-to-Action*.
- **Retrogradarea Sentimentului:** Deoarece am stabilit că sentimentul (Pozitiv/Neutru/Negativ) este mai puțin relevant pe Twitch, mută secțiunea cu aceste procente / graficul de tip Pie Chart într-un plan secundar (un panel mai mic, lateral sau într-o zonă inferioară a paginii), lăsând spațiul principal pentru *Hype Meter* și *Top Cuvinte*.

### 2. FILTRARE DINAMICĂ (Cross-Filtering)
- Modifică logica graficului de "Top Cuvinte Frecvente" (Bar Chart/Listă). 
- Când utilizatorul dă click pe un cuvânt din top (ex: "gg"), zona de chat în timp real trebuie să se filtreze instantaneu (pe partea de client, folosind JS) și să afișeze doar mesajele recente care conțin acel cuvânt. Un click din nou pe cuvânt va anula filtrul.

### 3. CONTROLUL FLUXULUI (Pause/Resume Chat)
- Adaugă un buton de "Pauză / Reia" lângă logul de chat live.
- La apăsarea lui, auto-scroll-ul chat-ului și randarea mesajelor noi pe ecran se opresc temporar, permițând utilizatorului să citească mesajele în liniște. (Atenție: Datele în fundal continuă să vină prin WebSockets și sunt stocate într-un buffer JS local, fiind afișate deodată când se apasă "Reia").

### 4. TOOLTIPS INTERACTIVE PE GRAFICE
- Îmbunătățește graficul "Hype Meter" (Evoluție Pozitivitate/Viteză).
- Când utilizatorul face *hover* (trece cu mouse-ul) peste un nod de pe graficul tip linie (Line Chart), afișează un tooltip personalizat care să nu arate doar valoarea numerică, ci și *cel mai reprezentativ mesaj* primit în acea secundă. (Vei avea nevoie ca worker-ul/backend-ul să trimită acest mesaj odată cu metrica respectivă).

### 5. DARK MODE TOGGLE
- Implementează un buton/switch sus în dreapta pentru a comuta între "Light Theme" și "Dark Theme".
- Tema întunecată ar trebui să folosească nuanțe de gri închis/negru cu accente de mov (`#9146FF` - culoarea specifică Twitch), text alb și culori adaptate pentru grafice (Chart.js / D3.js). Folosește variabile CSS (`:root`) pentru o tranziție curată.

Te rog să îmi oferi codul complet pentru `index.html` (cu CSS și structura DOM actualizată) și secțiunea de script JavaScript care gestionează logica pentru grafice, pauză, filtrare și comutarea temei.


# PROMPT: Faza 5 - Extinderea Funcționalităților LLM (Action Bar)

Ești un Senior Full-Stack Developer. Interfața aplicației noastre a fost rafinată (Dark Mode implementat, layout curat), însă dorim să maximizăm utilitatea spațiului gol de deasupra chat-ului, unde se află butonul de TL;DR.

Vrem să transformăm acea zonă într-un **"Action Bar"**, adăugând încă două butoane de analiză "On-Demand", care folosesc aceeași logică de Circular Buffer (Redis) și API-ul LLM (OpenAI/Echivalent) pe care am stabilit-o anterior.

Te rog să generezi codul actualizat pentru următoarele elemente:

### 1. BACKEND: Noi Endpoints pentru Analiză (Django `views.py`)
Adaugă două noi endpoint-uri care preiau ultimele mesaje din Redis și le trimit către LLM cu prompturi specifice:
- **Endpoint `/api/extract-questions/`:** Promptul pentru LLM va fi: *"Analizează aceste mesaje de pe un chat live. Extrage și listează doar întrebările clare și relevante adresate streamerului. Ignoră retorica sau spam-ul."*
- **Endpoint `/api/vibe-check/`:** Promptul pentru LLM va fi: *"Analizează starea de spirit a acestui chat. Răspunde cu un singur cuvânt sau o expresie scurtă care descrie vibe-ul general (ex: Trolling, Entuziasm/Hype, Confuzie, Chill, Toxic)."*

### 2. FRONTEND: UI pentru Action Bar (`index.html` și CSS)
- Grupează butonul existent de "Generează Context (TL;DR)" cu două butoane noi: **"Extrage Întrebări"** și **"Vibe Check"**.
- Stilizează acest grup sub forma unei bare de acțiuni elegante, aliniată orizontal în spațiul disponibil. Folosește culori distincte (sau iconițe) pentru a le diferenția, menținând tema Dark Mode (ex: accente discrete de mov, verde și albastru).

### 3. FRONTEND: Logica JS pentru noile acțiuni
- Scrie funcțiile JavaScript care fac request-uri asincrone (fetch) către noile endpoint-uri la apăsarea butoanelor.
- La fel ca la TL;DR, folosește un spinner de încărcare în timpul request-ului pentru a oferi feedback vizual.
- Afișează rezultatele într-un modal curat sau un panou de notificări. Pentru întrebări, afișează-le sub formă de listă (`<ul>`). Pentru Vibe Check, afișează rezultatul ca un "badge" (ecuson) evidențiat cu un text mărit.


# PROMPT: Faza 6 - Ajustarea Lexiconului VADER pentru Twitch Slang

Ești un Senior Full-Stack Developer și Data Scientist. Aplicația noastră monitorizează chat-ul de Twitch în timp real. Backend-ul folosește librăria `vaderSentiment` în `worker.py` pentru analiza de sentiment, dar ne lovim de o problemă clasică: VADER nu înțelege limbajul de gaming/Twitch, catalogând emote-urile și slang-ul (ex: "Pog", "LUL", "monkaS") drept cuvinte "Neutre" (scor 0).

Deoarece VADER este bazat pe lexicon, vreau să rezolvăm această problemă prin actualizarea dinamică a dicționarului său intern înainte de a procesa mesajele.

Te rog să actualizezi fișierul `worker.py` pentru a include următoarea logică:

### SARCINI PENTRU WORKER.PY:

1. **Definirea Lexiconului Twitch:**
   Creează un dicționar Python (ex: `TWITCH_LEXICON`) care să conțină termeni specifici platformei mapați la scoruri de valență (pe o scară de la aproximativ -4.0 la 4.0, așa cum folosește VADER). 
   
   Include (dar nu te limita la) următorii termeni și dă-le scoruri adecvate:
   - **Extrem/Foarte Pozitive (Scor +2.0 până la +3.5):** Pog, PogChamp, W, EZ, KEKW, LUL, LULW, OMEGALUL, POGGERS, 5Head, peepoHappy.
   - **Extrem/Foarte Negative (Scor -2.0 până la -3.5):** L, F, monkaS, monkaW, pepehands, Sadge, KEKWait, WeirdChamp, ResidentSleeper, cringe, throw, lag, trash.
   - **Atenție la Sarcasm/Context:** Cuvinte precum "Kappa" sau "Copium" pot avea o valoare ușor negativă sau neutră (ex: -0.5).

2. **Actualizarea Obiectului VADER:**
   La inițializarea obiectului `SentimentIntensityAnalyzer()`, adaugă o linie de cod care să injecteze dicționarul nostru personalizat în lexiconul nativ al modelului.
   *(Exemplu conceptual: `analyzer.lexicon.update(TWITCH_LEXICON)`).*

3. **Optimizare Pre-procesare (Opțional dar recomandat):**
   Deoarece chat-ul scrie deseori emote-urile incorect sau cu variații de litere mari/mici (ex: "pog", "POG", "Pog"), asigură-te că funcția de analiză normalizează textul (lowercase) SAU că dicționarul tău acoperă aceste variații (VADER este case-sensitive pentru intensificatori, deci asigură-te că potrivirea se face corect pentru slang).

Te rog să îmi oferi doar codul actualizat pentru `worker.py` (sau secțiunea relevantă din clasa/funcția de ML), arătând clar cum este definit și integrat acest dicționar Twitch în logica de `concurrent.futures`. Nu este nevoie să modifici Frontend-ul sau WebSockets-ul.

# PROMPT: Faza 7 & 8 - Reorganizare Analitică, Metrici Deterministice și Fundal Imersiv

Ești un Senior Full-Stack Developer. Dashboard-ul nostru pentru monitorizarea Twitch este funcțional, dar vrem să trecem la nivelul următor în materie de UX/UI și utilitate a datelor. Fereastra de chat brut ocupă prea mult spațiu, așa că o vom micșora pentru a face loc unor metrici algoritmice rapide (non-AI). De asemenea, vom adăuga imaginea de copertă (banner-ul) streamer-ului ca fundal al aplicației, pentru o experiență imersivă.

Te rog să actualizezi codul (`views.py`, `worker.py`, `index.html`, CSS și JS) pentru a implementa simultan următoarele funcționalități:

### 1. BACKEND - Extragere Banner (`views.py`)
- Când primești link-ul/numele canalului, scrie o funcție care încearcă să obțină URL-ul banner-ului/copertei streamer-ului (printr-un apel simplu de web scraping cu `BeautifulSoup` căutând meta tag-uri, sau printr-un API public fără OAuth).
- Trimite acest URL (sau un fallback) către frontend odată cu datele canalului.

### 2. BACKEND - Metrici Deterministice Fără AI (`worker.py`)
Modifică logica care procesează buffer-ul de mesaje din Redis pentru a calcula la fiecare secundă următoarele 3 statistici (pe baza ultimelor 50-100 de mesaje), și trimite-le prin WebSockets:
- **Audiență Organică (Unique Chatter Ratio):** Raportul procentual dintre numărul de username-uri unice și numărul total de mesaje.
- **Hype/Rage Thermometer (Caps-Lock Ratio):** Procentul de litere MAJUSCULE din totalul caracterelor alfabetice din mesajele recente.
- **Top 3 MVPs (Leaderboard):** Un top 3 al utilizatorilor care au trimis cele mai multe mesaje în fereastra curentă de timp.

### 3. FRONTEND - Reorganizare Layout și Fundal Dinamic (CSS & HTML)
- **Fundal Imersiv:** Creează un `<div id="dynamic-background"></div>` care acoperă tot ecranul (`100vw`, `100vh`, `position: fixed`, `z-index: -1`, `background-size: cover`). Aplică neapărat un efect pentru a menține textul lizibil: `filter: blur(15px) brightness(0.2);`.
- **Shrink Chat:** Redu lățimea (sau înălțimea) ferestrei de "Live Chat Log" la aproximativ 25-30% din spațiul disponibil.
- **Panou Nou:** În spațiul eliberat, creează panoul **"Statistici Viteza & Audiență"**.

### 4. FRONTEND - JavaScript (Actualizare UI Live)
- La schimbarea canalului, actualizează `backgroundImage` pentru `#dynamic-background` cu URL-ul banner-ului primit de la backend.
- Pentru **Audiență Organică**: Afișează valoarea venită de pe WebSocket printr-un Progress Bar (Verde peste 60%, Galben/Roșu dedesubt).
- Pentru **Caps-Lock Ratio**: Afișează un grafic de tip Gauge sau Bar Chart orizontal care se colorează în roșu la peste 40-50%.
- Pentru **Top 3 MVPs**: Afișează o listă ordonată (`<ol>`) curată cu numele și numărul de mesaje.

### 5. ACTUALIZARE DOCUMENTAȚIE
- Actualizează `README.md` (sau documentația sistemului) pentru a reflecta implementarea banner-ului dinamic și a noilor metrici heuristice (non-AI).
- Când creezi documentația finală, asigură-te că introduci exact următorul detaliu tehnic de context: plotul detaliat pentru Mercury arată eroarea scăzând la 0 în ziua 6 și apoi crescând din nou. Acest lucru se întâmplă deoarece plotul arată doar coordonata X, iar proiecțiile traiectoriilor RK4 și Simulink se intersectează în acel punct.

Te rog să îmi oferi secțiunile actualizate de CSS, logica de calcul din Python (`views.py` și `worker.py`) și funcțiile de manipulare DOM din JavaScript pentru noile metrici și fundal.