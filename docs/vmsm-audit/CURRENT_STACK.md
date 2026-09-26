# VM_SM — текущий стек

Источник: https://github.com/ivanovich1071/VM_SM- (коммит `438e795`, 26.09.2026). Около 3,3 тыс. строк Python + Jinja.

| Слой | Технология | Версия | Комментарий |
|------|-----------|--------|-------------|
| Язык | Python | 3.12 | |
| Веб | FastAPI + Uvicorn | 0.141 / 0.54 | Один процесс: веб + планировщик |
| Шаблоны | Jinja2 | 3.1.6 | Серверный рендер, без SPA |
| HTTP-клиент | httpx | 0.28.1 | Синхронный (`httpx.post`, `httpx.Client`) |
| Парсинг HTML | selectolax | 0.4.12 | Telegram `t.me/s`, сайты |
| БД | SQLite (WAL) | — | Сырой SQL, без ORM и миграций (`_add_column`) |
| Планировщик | APScheduler | 3.11 | `BackgroundScheduler`, cron по Europe/Minsk |
| Конфиг | python-dotenv + YAML | — | `config/competitors.yaml` засевает реестр |
| Markdown | Markdown | 3.10 | Рендер дайджеста |
| LLM | OpenRouter / Mistral | — | OpenAI-совместимый API, модель «провайдер:модель» |
| Instagram | Apify `instagram-profile-scraper` | — | По `APIFY_TOKEN` |
| Тесты | pytest | 9.1 | 6 файлов, HTML-фикстуры Telegram |
| Авторизация | HTTP Basic | — | Один пользователь (`DASH_USER`/`DASH_PASSWORD`) |
| Деплой | systemd + nginx на VPS | — | `/vmsm/` рядом с AI Business Auditor; Docker есть, не используется |
