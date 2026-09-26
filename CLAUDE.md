# CLAUDE.md — SM Content Intelligence SaaS

Справочный файл для Claude Code. Перед работой прочитай `CODING_AGENT_PROMPT.md` — там правила эпиков и жёсткие ограничения.

---

## 1. Назначение

Подписочный сервис для маркетологов любой ниши. Цепочка: рынок → данные о конкурентах → темы и Content Gaps →
аудит контента клиента → Content Opportunities → Контент Завод → публикация → performance → снова рынок.
Ценность — понять, что писать дальше, а не просто написать текст.

**Вне скоупа:** видео, озвучка/TTS, подкасты, AI-аватары, realtime-звонки, fine-tuning, CRM, white-label.

Продукт: `PRODUCT_SPEC.md`. Архитектура: `ARCHITECTURE.md`. Эпики: `ROADMAP.md`.
Основа — VM_SM (https://github.com/ivanovich1071/VM_SM-), что и как переносим — `docs/vmsm-audit/REUSE_MAP.md`.

---

## 2. Стек

| Слой | Технология |
|------|-----------|
| Backend | Python 3.11+, FastAPI, SQLAlchemy 2 async + asyncpg, Alembic, pydantic-settings |
| Очередь | arq на Redis 7 (`backend/app/workers/`) |
| БД | PostgreSQL 16 + pgvector (pgvector — с EPIC 3) |
| Auth | JWT access/refresh (PyJWT), bcrypt |
| AI | OpenRouter → Qwen 3.8 (Max для анализа и текстов, Flash для разметки и QA) через `AIRouter` |
| Frontend | Next.js 16 App Router, React 19, TypeScript, Tailwind v4 (палитра в `@theme` в `globals.css`), lucide-react |
| CI | GitHub Actions: ruff, alembic up/down, pytest, tsc, next build |

---

## 3. Структура

```
backend/app/
  core/          config (все ключи и Model ID), db, security (JWT/bcrypt), deps (get_tenant, require_role)
  models/        tenancy (organizations/users/memberships/invitations), billing (plans/subscriptions/usage_events),
                 system (jobs/llm_requests), sources (global_sources/sources/global_posts/post_metrics),
                 analysis (taxonomies/post_analysis/post_embeddings — pgvector)
  auth/          /auth/register|login|refresh|me
  organizations/ /organizations, /organizations/current(/members|/invitations)
  billing/       plans.py (тарифы в коде → upsert при старте), quotas.py (QuotaExceeded 402), usage.py, /billing/*
  ai/            openrouter.py (провайдер, 1 попытка), router.py (AIRouter: модель по задаче, квота, повторы,
                 схема, учёт), embeddings.py, prompts/
  jobs/          service (create/enqueue/set_status), /jobs, /jobs/ping, /jobs/{id}/cancel
  connectors/    base.py (SourceConnector, ContentItem, canonical_url, content_hash), http.py (Fetcher: лимит
                 на домен через Redis, запрет внутренних адресов), telegram.py (t.me/s из VM_SM), rss.py, website.py,
                 instagram.py (Apify, APIFY_TOKEN), youtube.py (RSS канала, без ключа), vk.py (VK_SERVICE_TOKEN)
  analysis/      taxonomy.py (универсальные поля + темы/роли организации, suggest), classify.py (Content Classifier,
                 пачки по 10), metrics.py (ER, медианы, overperformance), dedupe.py (hash/canonical + pgvector),
                 embed.py, pipeline.py (задача analyze_source), router.py (/taxonomy, /market/overview)
  competitors/   stats.py (статистика контента кодом), profile.py (Competitor Analyst, задача profile_competitor),
                 router.py (/competitors, /discover, /{id}/sources|profile|posts)
  posts/         router.py (/posts — лента с фильтрами и курсором, /posts/{id}, /posts/{id}/analyze), insight.py
                 (AI-разбор поста, кэш post_insights)
  topics/        stats.py (доли, gap, тренд — код), cluster.py (под-темы HDBSCAN, задача cluster_topics),
                 service.py (карточка темы, AI-объяснение gap), router.py (/topics, /gaps, /detail, /cluster)
  websites/      discover.py (robots, sitemap, типы страниц), diff.py (значимые строки, эвристика категории),
                 crawl.py (задача crawl_website), router.py (/websites, /pages, /changes, /crawl)
  audits/        criteria.py (критерии, веса, балл, эвристики), collect.py (каналы, сайт, сбор), benchmark.py
                 (метрики, рынок, gaps — код), pipeline.py (задача run_audit, Content Auditor), public.py (лимиты,
                 капча, частичный отчёт), router.py (/audits, /audits/claim, /public/audits)
  strategy/      candidates.py (темы-кандидаты, score, примеры — код), opportunities.py (задача build_opportunities,
                 Content Strategist, after_audit), router.py (/strategy/opportunities, /generate, PATCH статуса)
  factory/       formats.py (форматы и длины), brand.py (профиль и голос, подсказка), context.py (RAG), qa.py (проверки
                 кодом), pipeline.py (задача generate_content: write/edit/qa), export.py (.md/.html), router.py
                 (/brand, /factory/formats|projects|generate|versions|export)
  digests/       stats.py (цифры периода — код), render.py (шаблон, .md, HTML-письмо), mailer.py (SMTP),
                 generator.py (задача generate_digest, расписание, schedule_due), router.py (/digests, /schedule,
                 /export, /send)
  sources/       service.py (resolve/global_source/start_sync — общий для /sources и конкурентов), sync.py (sync_global_source, handle_sync_source, schedule_due), /sources CRUD + sync + posts
  workers/       settings.py (arq WorkerSettings + cron schedule_syncs), tasks.py (run_job, ping, sync_source →
                 analyze_source → profile_competitor)
backend/migrations/versions/0001_saas_core.py … 0011_digests.py
frontend/src/
  app/(auth)/login|register · app/(app)/<вкладки> · app/(app)/settings/<разделы> · app/free-audit (публичный аудит)
  lib/api.ts (fetch + refresh + X-Organization-Id), lib/auth.tsx (контекст), lib/nav.ts (меню и описания вкладок)
  components/ComingSoon.tsx (заглушки ещё не реализованных вкладок), PostList.tsx (посты с метриками и разметкой)
  lib/format.ts (иконки и названия площадок, форматирование чисел и дат)
```

---

## 4. Ключевые правила кода

- **Тенант:** tenant-таблица = `organization_id`. В эндпоинтах — `tenant: Tenant = Depends(get_tenant)` или
  `require_role(Role.member)`; выборки — `tenant.scoped(select(M), M)`, объект по id — `tenant.get(session, M, id)`
  (чужой → 404). Новый tenant-эндпоинт → проверка в `tests/test_tenancy.py`.
- **AI:** только `AIRouter(session).run(task, system, user, org_id=..., operation=..., schema=...)`.
  Не вызывать OpenRouter напрямую, не писать Model ID в модулях.
- **Квоты:** `quotas.check(session, org_id, "audits_month")` до постановки дорогой задачи; после — `usage.record(...)`.
- **Источники:** `global_sources`/`global_posts` общие для всех клиентов (канал собирается один раз), организация
  видит их через свою `sources`. Посты читать только через `Source` организации. Сеть в коннекторах — только через
  `Fetcher` (лимит на домен, SSRF-защита); в тестах — `httpx.MockTransport` и `check_hosts=False`.
- **Разметка:** `post_analysis` — tenant-таблица (у каждой организации своя таксономия); эмбеддинги и метрики поста
  общие. Правка таксономии повышает `version` — посты старой версии переразмечаются. Дубль (`duplicate_of_id`)
  скрывается и не размечается только для организации, которой виден оригинал — фильтр `dedupe.not_hidden(org_id)`
  обязателен во всех выборках постов. Промпты — `ai/prompts/<агент>/*.md`, загрузка `prompts.load("classifier/system")`.
- **Долгие операции:** `jobs.service.create_job` + `enqueue`, обработчик — через `workers.tasks.run_job`.
- **Числа считает код**, модель интерпретирует; при нехватке данных — явно «Недостаточно данных».
- Промпты — в `backend/app/ai/prompts/<агент>/`.
- Комментарии — только если неочевидно «почему». Коммиты — Conventional Commits.

---

## 5. Переменные окружения

См. `.env.example`. Главное: `SECRET_KEY`, `DATABASE_URL`, `REDIS_URL`, `OPENROUTER_API_KEY`,
`LLM_MODEL_ANALYZE|CLASSIFY|WRITE|QA`. Model ID сверять на https://openrouter.ai/models.

---

## 6. Запуск и тесты

```bash
docker compose up --build                       # всё сразу, миграции при старте api
cd backend && ruff check . && pytest -q         # тестам нужна БД sm_test (conftest пересоздаёт схему)
cd frontend && npx tsc --noEmit && npm run build
```

API: http://localhost:8000/docs · Web: http://localhost:3000 (проксирует `/api/*` на `API_URL`).

---

## 7. Статус

- ✅ EPIC 0 — аудит VM_SM (`docs/vmsm-audit/`)
- ✅ EPIC 1 — SaaS Core: регистрация, JWT, организации, роли, приглашения, тарифы и квоты, usage, AI Router,
  jobs + arq-воркер, `/health`, фронт (вход, меню всех вкладок, Команда, Тариф, Использование), 28 тестов, CI
- ✅ EPIC 2 — Source Layer: коннекторы Telegram / сайт / RSS / Instagram (Apify), `/sources`, задача `sync_source`, cron
  (Telegram и RSS — ежедневно, сайты и Instagram — еженедельно), UI «Настройки → Источники», 95 тестов
- ✅ EPIC 3 — Market Intelligence: таксономия организации (+ подсказка модели), разметка пачками, эмбеддинги
  (pgvector, HNSW), ER и overperformance, дубли (hash/canonical/семантические), `/market/overview`,
  UI «Компания» и блок рынка на «Обзоре», 111 тестов
- ✅ EPIC 4 — Конкуренты: CRUD, автопоиск соцсетей на сайте, AI-профиль, аналитика и таймлайн по неделям,
  коннекторы YouTube (без ключа) и VK (VK_SERVICE_TOKEN), UI список + карточка, 139 тестов
- ✅ EPIC 5 — Лента: фильтры, курсорная пагинация, поиск по смыслу и по словам, карточка поста (метрики к
  медиане, похожие посты), AI-разбор с кэшем, 151 тест
- ✅ EPIC 6 — Темы + Content Gaps: дерево «тема таксономии → под-темы (HDBSCAN)», доли, gap, тренд, насыщенность,
  карточка темы, AI-объяснение gap с идеями, блок gaps на «Обзоре», 162 теста
- ✅ EPIC 7 — Сайты: обход с robots.txt и sitemap, снимки страниц, «было/стало» без шума, смысл изменения от модели,
  новые и удалённые страницы, лимит страниц по тарифу, таймлайн конкурента, 182 теста
- ✅ EPIC 8 — Аудит контента: 6 критериев (балл — код, объяснения и evidence со ссылками на посты — модель),
  benchmark с порогом данных, gaps, прогресс по этапам, PDF через печать, публичный аудит без регистрации с
  лимитами и переносом в аккаунт, 197 тестов
- ✅ EPIC 9 — Content Strategy: 10 тем с цифрами рынка, пробелом клиента и примерами конкурентов (код) и
  формулировкой, углом и форматами (модель) с учётом аудита; вкладка «Стратегия», очередь в Контент Завод, 206 тестов
- ✅ EPIC 10 — Контент Завод: Brand Voice, RAG по рынку (pgvector), Writer → Editor → QA (код + модель), версии,
  статусы, экспорт .md/.html, очередь тем из «Стратегии», 215 тестов
- ✅ EPIC 11 — Дайджест: цифры периода кодом (рынок, темы, конкуренты и их сайты, «выстрелившие» посты, свой
  контент), выводы и идеи моделью, расписание, email (SMTP), .md/.html/PDF, 223 теста
- 🔲 Далее: EPIC 12 — Биллинг (см. `ROADMAP.md`)
