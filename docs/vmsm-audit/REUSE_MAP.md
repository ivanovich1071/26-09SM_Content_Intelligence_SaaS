# REUSE MAP — что берём из VM_SM

## Можно переиспользовать (перенос с минимальной адаптацией: async, organization_id)

| VM_SM | Новый модуль | Что сохранить | Что поменять |
|-------|-------------|---------------|--------------|
| `llm/client.py` | `backend/app/ai/openrouter.py`, `ai/router.py`, `ai/usage.py` | `parse_json`, повторы с backoff (429 → +8 c), без повтора на 4xx, `usage.include`, `reasoning.enabled=false`, запись битых ответов в расход | `httpx.AsyncClient`; учёт на организацию; модели по задачам analyze/classify/write/qa в конфиге |
| `collectors/telegram_web.py` | `connectors/telegram.py` | `parse_count`, `parse_channel_page`, `parse_feed`, пагинация `?before=`, задержка между страницами, HTML-фикстуры тестов | Возврат `ContentItem`, async, rate limit на домен |
| `collectors/site_watch.py` | `connectors/website.py`, `websites/diff.py` | `extract_text`, `extract_social`, `SOCIAL_SKIP`, `diff_lines` | Обход страниц, извлечение статей |
| `collectors/instagram.py` | `connectors/instagram.py` | `map_profile`, `shortcode_from_url` | Опционально по ключу |
| `analytics/metrics.py` | `analytics/metrics.py` | Медианы, `er_view`, `_share`, `topic_matrix` | SQLAlchemy, фильтр по организации |
| `audit/content_audit.py` | `audits/` | Нормализация `norm_telegram/instagram/site`, пайплайн сбор→разметка→метрики→бенчмарк→модель, общий балл считает код, `score: null` при нехватке данных | 6 критериев ТЗ, brand profile вместо VibeMind, «недостаточно данных для сравнения» |
| `factory.py` | `content/` | FORMATS с правилами длины, шаблоны `[в скобках]` вместо выдуманных фактов, источники тем | + LinkedIn, VK, Article; версии; QA; RAG |
| `analytics/digest.py` | `digests/` | Правило «только по данным», столпы контента как настройка | Позиционирование из brand profile |
| `scripts/eval_models.py` | `backend/scripts/eval_models.py` | Методика сравнения моделей | Eval-набор на несколько ниш |

## Нужно изменить
- Справочник `TAXONOMY` → универсальные поля + таксономия тем/ролей на организацию.
- `POSITIONING`, CTA, промпты → `brand_profiles` / `brand_voice`.
- Глобальные `settings` и лимит → настройки и квоты организации.

## Нужно переписать
- Слой БД (SQLite → PostgreSQL + SQLAlchemy async + Alembic).
- Планировщик и задачи (APScheduler + threading → arq + таблица `jobs`).
- Авторизацию (Basic → JWT, организации, роли).
- UI (Jinja → Next.js).

## Не трогать
- Сам репозиторий VM_SM и прод на `62.60.234.40/vmsm/`: он продолжает работать для VibeMind. Новый сервис — отдельный репозиторий;
  код переносится копированием с указанием источника, не через зависимость.
