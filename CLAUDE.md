# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture

Three Docker services orchestrated by `docker-compose.yml`, communicating over a shared virtual network:

```
Browser → Django (web:8000) → RabbitMQ (rabbitmq:5672) → Worker (worker)
                ↑                                              ↓
                └──────────── SQLite (shared volume /db) ─────┘
```

**`web_app/`** — Django 4.2 app. Handles HTTP, persists reviews to SQLite, publishes jobs to RabbitMQ as a producer. Single Django app: `analyzer`.

**`worker/`** — Standalone Python service. Consumes from RabbitMQ, runs ML inference in a `ProcessPoolExecutor` (one model loaded per process via initializer), writes results back to SQLite via raw `sqlite3`.

**`rabbitmq`** — `rabbitmq:3.12-management` image. Queue name: `reviews`, durable. Management UI on port 15672.

**Shared SQLite volume** (`db_data` → `/db/db.sqlite3`): mounted in both `web` and `worker` containers so the worker can update rows without going through the Django API.

## Data flow

1. User POSTs review → `analyzer/views.py:submit_review` saves row with `status=PENDING`, publishes `{id, text}` JSON to RabbitMQ.
2. Worker receives message → sets `status=PROCESSING` → submits text to `ProcessPoolExecutor`.
3. Process runs `ml_model.predict()` → worker callback sets `status=PROCESSED` + `sentiment`.
4. Dashboard polls `/api/status/<pk>/` every 3 s via JS fetch, updates badge and spinner in-place.

## Key files

| File | Role |
|------|------|
| `web_app/analyzer/models.py` | `Review` model — `status` (PENDING/PROCESSING/PROCESSED/FAILED), `sentiment` (POSITIVE/NEGATIVE/NEUTRAL) |
| `web_app/analyzer/views.py` | `submit_review` (producer), `dashboard`, `review_status` (JSON polling endpoint) |
| `worker/ml_model.py` | TF-IDF + Logistic Regression pipeline; `load_model()` trains+saves on first run, `predict(text, pipeline)` returns label |
| `worker/worker.py` | RabbitMQ consumer loop; `_init_worker` loads model once per process; `on_done` callback uses `add_callback_threadsafe` to ack on pika's thread |
| `docker-compose.yml` | Service definitions, volume mounts, env injection, healthcheck on RabbitMQ |
| `.env` | `RABBITMQ_USER/PASS`, `DJANGO_SECRET_KEY`, `DEBUG` — never commit |

## Development commands

```bash
# Pornire completă (prima dată sau după modificări de cod/Dockerfile)
docker compose up --build

# Pornire fără rebuild
docker compose up

# Oprire și curățare volume (resetează DB și modelul ML salvat)
docker compose down -v

# Loguri live pentru un singur serviciu
docker compose logs -f web
docker compose logs -f worker
docker compose logs -f rabbitmq

# Shell în containerul Django (ex: pentru migrate manual sau crearea unui superuser)
docker compose exec web bash
python manage.py createsuperuser

# Rulare migrate manual
docker compose exec web python manage.py migrate
```

## RabbitMQ Management UI

Accesibil la `http://localhost:15672` cu credențialele din `.env` (`RABBITMQ_USER` / `RABBITMQ_PASS`).  
Secțiunea **Queues → reviews** arată mesajele în așteptare (`Ready`) și cele în procesare (`Unacked`).

## Settings propagation

All runtime config flows through environment variables injected by Docker Compose from `.env`. In Django, RabbitMQ params are read in `settings.py` and accessed as `settings.RABBITMQ_*`. In the worker, they are read directly via `os.environ.get()`.
