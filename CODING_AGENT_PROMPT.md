# CODING AGENT PROMPT

Ты — кодинг-агент проекта **SM Content Intelligence SaaS**. Перед работой прочитай `CLAUDE.md`, `PRODUCT_SPEC.md`,
`ARCHITECTURE.md`, `ROADMAP.md` и `docs/vmsm-audit/REUSE_MAP.md`.

## Как работать
1. Бери **один** эпик из `ROADMAP.md` (первый со статусом 🔲). Не начинай следующий, пока текущий не готов.
2. Перед кодом найди в `docs/vmsm-audit/REUSE_MAP.md`, что переносится из VM_SM
   (https://github.com/ivanovich1071/VM_SM-). Переноси код с сохранением проверенной логики и тестов-фикстур,
   адаптируя под async и `organization_id`. В docstring модуля укажи источник («перенесено из VM_SM app/…»).
3. Делай vertical slice: миграция Alembic → модели → схемы → сервис → API → job → UI → тесты.
4. После эпика:
   - `cd backend && ruff check . && pytest -q`
   - `cd frontend && npx tsc --noEmit && npm run build`
   - `docker compose up --build`, проверь `/health`, пройди сценарий эпика в браузере
   - просмотри логи api/worker
   - обнови статус в `ROADMAP.md` и раздел «Статус» в `CLAUDE.md`
   - коммит в стиле Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`)
5. Критическая регрессия → чини, не иди дальше.

## Жёсткие правила
- **Мультитенантность:** каждая tenant-таблица имеет `organization_id`; все запросы — через `tenant.scoped()`;
  чужой ресурс → 404. На каждый новый tenant-эндпоинт — строка в `tests/test_tenancy.py`.
- **AI только через `AIRouter`.** Никаких прямых вызовов OpenRouter и Model ID в коде модулей.
  Каждый вызов пишет `llm_requests` и `usage_events`. Ответ модели — JSON, валидируется Pydantic-схемой.
- **Промпты** — в `backend/app/ai/prompts/<агент>/*.md`, не строками в коде.
- **Числа считает код.** Модель не придумывает метрики и benchmark; при малой выборке — «Недостаточно данных для
  надёжного рыночного сравнения».
- **Контент конкурентов** — контекст для анализа, не для копирования.
- **Долгие операции** — только jobs (arq) со статусами; API возвращает `job_id`.
- **Квоты** проверяются до постановки дорогой задачи (`QuotaService.check`).
- **Секреты** — только `.env`, никогда в git.
- Не делать: видео, аудио/TTS, подкасты, аватары, realtime, fine-tuning, CRM, white-label.
- Не трогать рабочий VM_SM и его деплой.
