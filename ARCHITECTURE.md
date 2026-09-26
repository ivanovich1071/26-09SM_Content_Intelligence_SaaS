# ARCHITECTURE — SM Content Intelligence SaaS

## 1. Компоненты

```
┌──────────────────────────┐        ┌──────────────────────────────────────────────┐
│ web: Next.js 15 (TS)     │  REST  │ api: FastAPI                                  │
│ App Router + Tailwind v4 ├───────►│ auth · organizations · billing(quotas/usage)  │
└──────────────────────────┘  JWT   │ sources · competitors · posts · topics ·      │
                                    │ websites · audits · content · digests · admin │
                                    └──────┬─────────────────────────┬──────────────┘
                                           │ enqueue (arq)           │ SQLAlchemy async
                                           ▼                         ▼
                                    ┌──────────────┐        ┌────────────────────────┐
                                    │ worker (arq) │───────►│ PostgreSQL 16+pgvector │
                                    │ jobs pipeline│        │ Redis 7 (очередь, кэш) │
                                    └──────┬───────┘        └────────────────────────┘
                                           │
                        ┌──────────────────┼────────────────────┐
                        ▼                  ▼                    ▼
                   connectors/        ai/router ──► OpenRouter (Qwen3.8 Max / Flash)
              telegram, website,      ai/embeddings ──► EmbeddingProvider
              rss, instagram, vk,     ai/prompts/*
              youtube, mcp/*
```

Принципы:
- **MCP — адаптер, не бизнес-слой**: `Application → Connector abstraction → Provider / MCP adapter`.
- **Числа считает код, модель интерпретирует.** Метрики, benchmark и общий балл — детерминированный код.
- **Каждый AI-ответ — JSON, валидируется Pydantic-схемой** до сохранения; при невалидном ответе — повтор, затем ошибка задачи.
- **Нет одного огромного агента.** Логические агенты = функции/воркфлоу с собственным промптом и схемой.

## 2. Логические AI-агенты

| # | Агент | Модуль | Модель (задача router) | Вход → выход |
|---|-------|--------|------------------------|--------------|
| 01 | Orchestrator | `workers/pipelines.py` | — (код) | Цепочки задач, статусы |
| 02 | Source Collector | `connectors/*` | — | Источник → ContentItem[] |
| 03 | Content Classifier | `posts/classification.py` | classify | Пост → классификация |
| 04 | Competitor Analyst | `competitors/profile.py` | analyze | Посты+сайт → профиль конкурента |
| 05 | Market Analyst | `analytics/market.py` | analyze | Метрики → выводы по рынку |
| 06 | Topic Analyst | `topics/clustering.py` | classify | Кластеры → названия/под-темы |
| 07 | Trend Analyst | `topics/trends.py` | — (код) + analyze | Динамика → растущие темы |
| 08 | Content Auditor | `audits/pipeline.py` | analyze | Данные → 6 критериев |
| 09 | Content Strategist | `strategy/opportunities.py` | analyze | Кандидаты кода (gap, тренд, ER) + аудит → 10 opportunities |
| 10 | Content Writer | `factory/pipeline.py` + `prompts/writer` | write | RAG-контекст + бренд → черновик |
| 11 | Content Editor | `factory/pipeline.py` + `prompts/editor` | write | Версия + инструкция + замечания QA → версия |
| 12 | Content QA | `factory/qa.py` (код) + `prompts/qa` | qa | Текст + контекст → проверки |
| 13 | Digest Generator | `digests/generator.py` + `prompts/digest` | analyze | Цифры периода (код) → выводы, рекомендации, идеи |

## 3. AI Router

```python
class AIProvider(Protocol):
    async def chat_json(self, model, system, user, *, temperature, max_tokens) -> AIResult: ...

class AIRouter:
    async def run(self, task: Literal["analyze","classify","write","qa"], system, user, *,
                  org_id, operation, job_id=None, schema: type[BaseModel] | None = None) -> dict
```
- Модель по задаче из конфига (`LLM_MODEL_ANALYZE`, `LLM_MODEL_CLASSIFY`, `LLM_MODEL_WRITE`, `LLM_MODEL_QA`),
  организация может переопределить в Enterprise. Model ID больше нигде в коде не упоминается.
- До вызова: проверка квоты `ai_cost_usd_month` организации → `QuotaExceeded` (HTTP 402).
- После вызова: `llm_requests` (сырые токены, модель, латентность, ok) + `usage_events` (операция, стоимость).
- Повторы: 429/5xx/битый JSON — до 3 раз с backoff; 4xx — без повтора. Fallback-модель по задаче (опционально).

`EmbeddingProvider.embed(texts) -> list[list[float]]` — по умолчанию `openai/text-embedding-3-small` (1536),
провайдер задаётся конфигом, проект не привязан к OpenAI.

## 4. Source Layer

```python
class SourceConnector(ABC):
    kind: str
    async def validate_source(self, source) -> SourceValidation
    async def collect(self, source, cursor: str | None = None) -> CollectResult  # items + next_cursor
    async def get_profile(self, source) -> SourceProfile
    async def get_metrics(self, item) -> ItemMetrics
    async def health_check(self, source) -> HealthStatus
```
Реализованная сигнатура (EPIC 2) — методы получают `Fetcher`, ключ и URL глобального источника:
`normalize(raw) -> (key, url)` без сети, `get_profile(http, key, url, meta)`,
`collect(http, key, url, meta, since=, known_ids=) -> CollectResult`. Сеть — только через `connectors/http.Fetcher`:
пауза между запросами к домену (Redis `SET NX PX`, общий для воркеров), ручные редиректы с проверкой, что адрес
публичный (SSRF), ограничение размера ответа. `WebsiteConnector` находит RSS/Atom блога (autodiscovery и типовые пути).

Адаптеры: `TelegramConnector` (t.me/s из VM_SM), `WebsiteConnector`, `RSSConnector`, `YouTubeConnector`,
`VKConnector`, `InstagramConnector` (Apify), `SearchConnector`. Provider-specific данные остаются в `raw_payload`.

**ContentItem:** id, organization_id (через привязку), source_id, author, url, canonical_url, title, text,
published_at, media_type, metrics{views,likes,comments,shares}, content_hash, raw_payload.

**Дедупликация:** уникальность (source_id, external_id); canonical_url + content_hash между источниками;
семантические дубли — косинус эмбеддингов ≥ 0,95 в окне ±3 дня.

**Общие публичные источники:** один и тот же публичный канал у 100 клиентов собирается один раз
(`global_sources`, `global_posts`); `sources` организации ссылается на глобальный источник. Классификация по
таксономии организации хранится в `post_analysis` с `organization_id`.

## 5. Пайплайны (jobs)

| Job | Шаги |
|-----|------|
| `sync_source` | collect → normalize → dedupe → save → enqueue(classify, embed, metrics) |
| `crawl_website` | fetch pages → extract text/articles → snapshot → diff → AI смысл изменения |
| `analyze_source` (реализовано, EPIC 3) | метрики → точные дубли → разметка (пачки по 10) → эмбеддинги → смысловые дубли |
| `classify_posts` | пачки по 20 → Classifier → валидация → `post_analysis` |
| `generate_embeddings` | тексты без эмбеддинга → EmbeddingProvider → pgvector |
| `cluster_topics` | эмбеддинги за 90 дней → кластеризация → Topic Analyst → `topics` |
| `calculate_metrics` | ER, медианы по источнику, overperformance |
| `run_audit` | источники → сбор → классификация → метрики → benchmark → Auditor; после — `build_opportunities` |
| `build_opportunities` | кандидаты тем (код) → Strategist → `content_opportunities` |
| `generate_content` | write: RAG-контекст → Writer → QA → версия; edit: Editor → QA → версия; qa: перепроверка |
| `run_content_qa` | QA → отчёт проверок |
| `generate_digest` | период → цифры (код) → Digest Generator / шаблон → email по SMTP; cron `schedule_digests` ежечасно |

Статусы: `queued → running → collecting → analyzing → generating → validating → completed | failed | cancelled`.
В `jobs`: stage, progress 0–100, error, result_ref. Фронт опрашивает `GET /jobs/{id}` (позже SSE).

## 6. Схема БД (целевая)

Все tenant-owned таблицы содержат `organization_id` (FK, индекс).

| Группа | Таблицы |
|--------|---------|
| Tenancy | `organizations`, `users`, `memberships(role: owner/admin/member/viewer)`, `invitations` |
| Billing | `plans(limits JSON)`, `subscriptions`, `usage_events` |
| Sources | `global_sources`, `sources`, `competitors`, `social_accounts`, `websites`, `website_pages`, `page_snapshots`, `website_changes` |
| Content | `global_posts`, `post_metrics`, `post_analysis`, `post_embeddings(vector)` |
| Topics | `taxonomies`, `topics`, `topic_clusters`, `trends` |
| Audit | `content_audits` (результат: метрики, benchmark, gaps, проблемы — JSON), `audit_items` (критерий: балл, пояснение, evidence, советы); `content_opportunities` (тема, почему, угол, форматы, цифры рынка, примеры, статус) |
| Brand | `brand_profiles` (профиль компании и голос бренда в одной записи) |
| Factory | `content_projects`, `content_versions` (поля, контекст RAG, QA); генерации — `usage_events` + `llm_requests` |
| Digest | `digests`, `digest_schedules` |
| System | `jobs`, `llm_requests`, `admin_actions` (журнал действий суперадминов) |

Реализовано в миграции 0001: organizations, users, memberships, plans, subscriptions, usage_events, jobs, llm_requests.
Миграция 0002: global_sources, sources, global_posts, post_metrics.
Миграция 0007: websites, website_pages, page_snapshots, website_changes.
Миграция 0006: topic_clusters, topic_cluster_posts, topic_insights.
Миграция 0012: admin_actions; subscriptions.limits_override, subscriptions.note; users.last_seen_at.
Миграция 0005: post_insights (AI-разбор поста, кэш на организацию).
Миграция 0004: competitors, sources.competitor_id, source_kind += youtube, vk.
Миграция 0003: taxonomies, post_analysis, post_embeddings (vector(1536), HNSW cosine); метрики в global_posts
(engagement, er, overperformance, duplicate_of_id) и базовая линия в global_sources (median_views, median_engagement).

## 7. Мультитенантность
1. JWT содержит `sub` (user_id); активная организация — заголовок `X-Organization-Id` (или первая доступная).
2. Зависимость `get_tenant()` проверяет членство и возвращает `Tenant(org_id, user_id, role)`.
3. Все запросы к tenant-таблицам идут через `tenant.scoped(select(Model))`, который добавляет фильтр `organization_id`.
   Чужой ресурс → 404 (не 403, чтобы не раскрывать существование).
4. Row Level Security в Postgres (`SET app.org_id`) — вторая линия, EPIC 12.
5. Тест `test_tenancy.py` проверяет изоляцию на каждом tenant-эндпоинте.

## 8. Роли

| Действие | Owner | Admin | Member | Viewer |
|----------|:-----:|:-----:|:------:|:------:|
| Просмотр данных | ✓ | ✓ | ✓ | ✓ |
| Источники, конкуренты, генерация, аудит | ✓ | ✓ | ✓ | |
| Команда, приглашения | ✓ | ✓ | | |
| Тариф, удаление организации | ✓ | | | |

## 9. Конфигурация
Все секреты и Model ID — через переменные окружения (`backend/app/core/config.py`, pydantic-settings). См. `.env.example`.
