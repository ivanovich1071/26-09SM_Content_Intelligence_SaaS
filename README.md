# SM Content Intelligence SaaS

Подписочный сервис для маркетологов любой ниши: мониторинг рынка и конкурентов, темы и Content Gaps,
аудит контента (лид-магнит) и Контент Завод. AI — Qwen через OpenRouter.

Выросло из внутреннего инструмента [VM_SM](https://github.com/ivanovich1071/VM_SM-): аудит и план переноса — в `docs/vmsm-audit/`.

| Документ | О чём |
|----------|-------|
| [PRODUCT_SPEC.md](PRODUCT_SPEC.md) | Вкладки, сценарии, критерии аудита, тарифы |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Компоненты, AI-агенты, AI Router, коннекторы, схема БД, мультитенантность |
| [ROADMAP.md](ROADMAP.md) | Эпики 0–13 и критерии готовности |
| [CODING_AGENT_PROMPT.md](CODING_AGENT_PROMPT.md) | Правила для кодинг-агента |
| [docs/vmsm-audit/](docs/vmsm-audit/) | Аудит VM_SM: стек, маршруты, БД, функции, REUSE/RISK/MIGRATION |

## Запуск через Docker

```bash
cp .env.example .env        # заполнить SECRET_KEY и OPENROUTER_API_KEY
docker compose up --build
```

- Web: http://localhost:3000
- API: http://localhost:8000 · Swagger: http://localhost:8000/docs · `/health`

Миграции применяются при старте контейнера `api`.

## Локальная разработка

```bash
# PostgreSQL 16 и Redis 7 должны быть запущены (или: docker compose up postgres redis)
cd backend
python -m venv .venv && .venv/bin/pip install -e ".[dev]"   # Windows: .venv\Scripts\pip
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload --port 8000
.venv/bin/arq app.workers.settings.WorkerSettings            # воркер фоновых задач, отдельный терминал

cd frontend
npm install
npm run dev                                                  # /api/* проксируется на API_URL (по умолчанию :8000)
```

## Проверки

```bash
cd backend && ruff check . && pytest -q         # нужна БД sm_test (DATABASE_URL переопределяет)
cd frontend && npx tsc --noEmit && npm run build
```
