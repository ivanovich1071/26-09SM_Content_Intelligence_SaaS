# VM_SM — база данных (SQLite, `app/db.py`)

Схема создаётся `CREATE TABLE IF NOT EXISTS`; миграции — ручные `ALTER TABLE` через `_add_column`. Нет `organization_id`.

| Таблица | Назначение | Ключевые поля | Что станет в SaaS |
|---------|-----------|---------------|-------------------|
| `accounts` | Аккаунты соцсетей | company, kind (self/competitor/market), platform, handle, status, enabled, ui_edited | `sources` + `competitors` (+ `global_sources` для общих публичных каналов) |
| `account_snapshots` | Подписчики по дням | account_id, taken_at, followers | `source_snapshots` |
| `posts` | Публикации | external_id, published_at, url, format, text, links, views/likes/comments/shares | `posts` (ContentItem) + `content_hash`, `canonical_url` |
| `post_metrics_snapshots` | История метрик поста | post_id, taken_at, … | `post_metrics` |
| `post_class` | Разметка моделью | content_type, topic, target_role, funnel_stage, proof_type, cta_type, флаги, ai_tools, summary | `post_analysis` (на организацию, по её таксономии) |
| `site_snapshots` | Снимки текста сайтов | url, text_hash, text, social_links | `website_pages` / `page_snapshots` |
| `site_changes` | Diff сайтов | added/removed_lines, new/lost_social | `website_changes` |
| `digests` | Дайджесты | period, body_md, stats | `digests` |
| `content_ideas` | Идеи из дайджеста | title, pillar, target_role, rationale, draft, status | `content_opportunities` |
| `sites` | Реестр сайтов | company, url, enabled | `websites` |
| `settings` | Ключ-значение | key, value | `organization_settings` |
| `llm_usage` | Расход LLM | task, model, tokens, cost_usd, ok | `llm_requests` + `usage_events` |
| `content_audits` | Аудиты | company, site, telegram, instagram, metrics, result, score, status | `content_audits` + `audit_items` + `audit_recommendations` |
| `content_pieces` | Тексты Завода | topic, source, format, body, status | `content_projects` + `content_versions` |
| `runs` | Журнал задач | job, started/finished, ok, message | `jobs` (со статусами этапов) |
