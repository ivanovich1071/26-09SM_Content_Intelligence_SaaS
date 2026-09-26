# ROADMAP — эпики и критерии готовности

Принцип: vertical slices — каждый эпик даёт работающий функционал (DB + API + worker + UI + тесты).
После каждого эпика: тесты → запуск → проверка API и фронта → логи → документация → коммит.
Критическая регрессия — стоп, к следующему эпику не переходим.

| # | Эпик | Статус |
|---|------|--------|
| 0 | Аудит VM_SM | ✅ `docs/vmsm-audit/` |
| 1 | SaaS Core | ✅ каркас (см. ниже) |
| 2 | Source Layer | ✅ Telegram, сайт (через RSS блога), RSS, Instagram (Apify); sync-задачи и cron |
| 3 | Market Intelligence | 🔲 |
| 4 | Конкуренты | 🔲 |
| 5 | Лента | 🔲 |
| 6 | Темы + Content Gaps | 🔲 |
| 7 | Сайты | 🔲 |
| 8 | Аудит контента (лид-магнит) | 🔲 |
| 9 | Content Strategy / Opportunities | 🔲 |
| 10 | Контент Завод | 🔲 |
| 11 | Дайджест | 🔲 |
| 12 | Биллинг + RLS | 🔲 |
| 13 | Админка | 🔲 |

MVP = эпики 1–10 + usage + ручное назначение тарифа.

---

## EPIC 1 — SaaS Core ✅
- Регистрация (создаёт организацию, роль owner, подписку Free), логин, refresh, `/auth/me`.
- Организации: список моих, текущая, участники, приглашение по email (owner/admin), смена роли, удаление участника.
- Тарифы (seed: free, starter, professional, agency, enterprise) с лимитами JSON; `GET /billing/plan`, `GET /billing/usage`.
- `QuotaService.check(org, metric, amount)` → 402.
- AI Router + OpenRouter-клиент + учёт `llm_requests`/`usage_events`.
- Jobs: таблица, arq-воркер, `GET /jobs`, `GET /jobs/{id}`, тестовая задача `ping`.
- `/health` (db, redis).
- Frontend: логин/регистрация, app-shell с меню, страницы-заглушки, «Использование», «Команда».
- Тесты: auth, роли, cross-tenant, AI router (мок), квоты.

## EPIC 2 — Source Layer
- `connectors/base.py` (SourceConnector, ContentItem), `telegram.py` (перенос `telegram_web.py`), `website.py`, `rss.py`.
- Таблицы `global_sources`, `sources`, `global_posts`, `post_metrics` (миграция 0002).
- API: `POST/GET /sources`, `DELETE /sources/{id}`, `POST /sources/{id}/sync`, `GET /sources/{id}/status`.
- Job `sync_source`; расписание (arq cron): Telegram ежедневно, сайты еженедельно.
- Rate limit на домен (Redis token bucket).
- UI: Настройки → Источники (добавить, статус, синхронизировать).
- Готово когда: добавил `t.me/<канал>` → через job в БД есть посты с метриками; фикстуры VM_SM проходят.
- ✅ Сделано. Дополнительно: защита от запросов во внутреннюю сеть (адреса вводят пользователи), общий кэш
  сборов — канал, собранный у другого клиента меньше 30 мин назад, повторно не качается.
  Instagram — через Apify (`APIFY_TOKEN` на сервере, один на всех клиентов, обновление раз в неделю).
  Перенесено: VK, YouTube (по ключам) — вместе с EPIC 4; снимки страниц сайта — EPIC 7.

## EPIC 3 — Market Intelligence
- Нормализация, дедупликация (hash + canonical), `post_analysis` (миграция 0003, pgvector).
- Таксономия организации: универсальные поля фиксированы; темы/роли — предлагает модель при онбординге, правит пользователь.
- Jobs: `classify_posts`, `generate_embeddings`, `calculate_metrics` (ER, медиана источника, overperformance).
- Готово когда: после sync посты классифицированы, у каждого есть эмбеддинг и overperformance.

## EPIC 4 — Конкуренты
- CRUD, автопоиск соцсетей на сайте (`extract_social`), создание sources.
- Competitor Analyst → профиль (позиционирование, ЦА, темы, форматы, ToV, частота, CTA, паттерны).
- UI: список, карточка (профиль, аналитика, таймлайн, контент). Лимит конкурентов по тарифу.

## EPIC 5 — Лента
- `GET /posts` с фильтрами и курсорной пагинацией; семантический поиск (pgvector).
- `GET /posts/{id}` + `POST /posts/{id}/analyze` (AI-разбор поста, кэшируется).
- UI: лента с фильтрами, карточка поста.

## EPIC 6 — Темы + Content Gaps
- `cluster_topics` (HDBSCAN по эмбеддингам, названия и под-темы от модели), `trends` (рост доли за период).
- Насыщенность (публикаций конкурентов на тему), gap = доля рынка − доля клиента.
- API `/topics`, `/topics/{id}`, `/topics/gaps` + AI-объяснение gap.
- UI: Topic Explorer, дерево тем, полосы gap.

## EPIC 7 — Сайты
- Обход (sitemap + ссылки, лимит страниц по тарифу), извлечение статей, снимки, diff, AI-смысл изменения.
- UI: список сайтов, изменения «было/стало».

## EPIC 8 — Аудит контента
- Пайплайн из VM_SM, 6 критериев ТЗ, evidence со ссылками на посты, benchmark с порогом данных.
- Публичный `/audit` без регистрации (лимит по IP, капча), результат частично скрыт.
- UI: форма, прогресс по этапам, отчёт (общий балл, критерии, проблемы, gaps), PDF.

## EPIC 9 — Content Strategy
- Content Strategist: 10 opportunities (почему, market evidence, customer gap, примеры конкурентов, форматы).
- UI: список opportunities → «В Контент Завод».

## EPIC 10 — Контент Завод
- Brand profile и brand voice (Настройки; модель предлагает черновик по сайту).
- RAG: лучшие посты по теме, паттерны, конкуренты, прошлый контент клиента, рекомендации аудита.
- Writer → Editor (правки по инструкции) → QA (факты вне контекста, близость к постам конкурентов, длина, CTA, тон).
- Версии, статусы, экспорт (копировать, .md, .html для email).
- Форматы: Telegram, Email, LinkedIn, VK, Article.

## EPIC 11 — Дайджест
- Еженедельный/ежемесячный/свой cron; web + email (SMTP) + PDF.

## EPIC 12 — Биллинг
- `PaymentProvider` (bePaid / ЮKassa / Stripe), планы, overage, счета, вебхуки. RLS в Postgres.

## EPIC 13 — Админка
- Пользователи, организации, подписки (ручная смена тарифа), расход LLM по организациям, задачи, ошибки, провайдеры.
