# VM_SM — маршруты (`app/main.py`)

Все маршруты за HTTP Basic (если задан `DASH_PASSWORD`). HTML-страницы рендерятся Jinja.

| Метод | Путь | Вкладка / назначение |
|-------|------|----------------------|
| GET | `/` | Обзор: KPI, таблица аккаунтов, публикации по неделям, последние запуски |
| GET | `/competitors` | Конкуренты: реестр аккаунтов и сайтов |
| POST | `/competitors/account` | Добавить аккаунт |
| POST | `/competitors/account/{id}/toggle` | Вкл/выкл аккаунт |
| POST | `/competitors/site` | Добавить сайт |
| POST | `/competitors/site/{id}/toggle` | Вкл/выкл сайт |
| GET | `/posts` | Лента: посты с разметкой и фильтрами |
| GET | `/topics` | Темы: матрица «тема × роль» |
| GET | `/sites` | Сайты: изменения текста и соцсетей |
| GET | `/plan` | Дайджест + контент-план (идеи) |
| POST | `/ideas/{id}` | Статус идеи |
| GET/POST | `/instagram` | Ручной ввод метрик Instagram |
| POST | `/run/{job}` | Ручной запуск задачи (telegram, instagram, sites, classify, digest) |
| GET | `/audit` | Аудит контента: форма + список аудитов |
| POST | `/audit` | Запуск аудита (поток) |
| GET | `/audit/{id}` | Результат аудита |
| GET | `/api/audit/{id}` | Статус аудита JSON (опрос со страницы) |
| GET | `/factory` | Контент Завод: источники тем + сгенерированные тексты |
| POST | `/factory/generate` | Генерация текста |
| POST | `/pieces/{id}` | Правка/статус текста |
| GET/POST | `/settings` | Модели, лимит расходов, проверка ключа OpenRouter |
| GET | `/api/status` | Состояние задач |

Шапка (`base.html`): Обзор · Конкуренты · Лента · Темы · Сайты · План · Аудит контента · Контент Завод · Настройки,
плюс внешние ссылки на AI Business Auditor.
