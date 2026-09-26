# MIGRATION PLAN — VM_SM → SM Content Intelligence SaaS

Принцип: VM_SM не переписывается на месте. Новый сервис — отдельный репозиторий; модули VM_SM переносятся
по одному, каждый с тестами, в том эпике, где он нужен.

| Шаг | Эпик | Что переносим | Проверка |
|-----|------|---------------|----------|
| 1 | EPIC 1 | `llm/client.py` → `ai/openrouter.py` + `ai/router.py` + `ai/usage.py` | Тест с моком HTTP: JSON, повтор на 429, расход в `llm_requests`/`usage_events`, стоп по квоте |
| 2 | EPIC 2 | `telegram_web.py` → `connectors/telegram.py`; `site_watch.py` → `connectors/website.py` | Старые HTML-фикстуры проходят через новый парсер с теми же результатами |
| 3 | EPIC 3 | `classify.py` → `ai/prompts/classifier` + `posts/classification.py` | eval-набор VM_SM: точность не ниже, чем в VM_SM |
| 4 | EPIC 3 | `metrics.py` → `analytics/metrics.py` | Тесты медиан/ER на фикстурах |
| 5 | EPIC 8 | `content_audit.py` → `audits/` | Аудит VibeMind в новом сервисе даёт сопоставимые оценки |
| 6 | EPIC 10 | `factory.py` → `content/` | Генерация в 5 форматах, QA |
| 7 | EPIC 11 | `digest.py` → `digests/` | Дайджест на данных демо-организации |
| 8 | после MVP | Данные VibeMind | Скрипт импорта SQLite VM_SM → организация «VibeMind» в SaaS (accounts, posts, post_class, audits) |

После шага 8 VM_SM можно вывести из эксплуатации: VibeMind становится первым клиентом SaaS.

## Итоговый отчёт

- **WHAT CAN BE REUSED:** LLM-клиент, коллекторы Telegram/сайтов/Instagram, метрики, логика аудита, форматы Завода, eval-скрипт.
- **WHAT MUST BE CHANGED:** таксономия, позиционирование и промпты (на организацию), настройки и лимиты (на организацию).
- **WHAT MUST BE REWRITTEN:** БД, задачи/планировщик, авторизация, весь UI.
- **WHAT MUST NOT BE TOUCHED:** рабочий VM_SM и его деплой до шага 8.
