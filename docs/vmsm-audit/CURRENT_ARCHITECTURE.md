# VM_SM — текущая архитектура

```
             APScheduler (в процессе)                       Браузер (Basic Auth)
   06:00 telegram · пн 07:00 instagram · пн 08:00 sites         │
          пн 09:00 digest                                        ▼
                 │                                  FastAPI app/main.py (Jinja-страницы)
                 ▼                                               │
          app/jobs.py (_run + threading.Lock + runs)             │
                 │                                               │
 ┌───────────────┼─────────────────────┐                         │
 ▼               ▼                     ▼                         ▼
collectors/   llm/classify.py     analytics/digest.py     audit/content_audit.py · factory.py
telegram_web  (TAXONOMY, 4 потока) (дайджест + 5 идей)     (аудит 5 критериев)   (тексты)
instagram           │                     │                       │
site_watch          └──────── llm/client.py (chat_json, лимит $, llm_usage) ──────┘
      │                                   │
      └──────────────► SQLite (app/db.py) ◄┘
```

## Ключевые свойства

- **Один тенант.** Всё про VibeMind: позиционирование `POSITIONING` зашито в `analytics/digest.py` и импортируется аудитом и Заводом.
- **Реестр из YAML.** `config/competitors.yaml` → `accounts`/`sites`; правки в UI помечаются `ui_edited=1` и YAML их не перезаписывает.
- **LLM-слой** (`llm/client.py`): провайдеры openrouter/mistral, модели по задачам `classify` и `write`, выбор модели
  в «Настройках» перекрывает `.env`; каждый вызов пишется в `llm_usage` со стоимостью (`usage.cost` от OpenRouter или
  по прайсу); месячный лимит останавливает вызовы, сбор продолжается. `reasoning: {enabled: false}` — иначе flash-модели
  обрезают JSON.
- **Разметка** (`llm/classify.py`): фиксированный справочник (тип, тема, роль, стадия воронки, доказательства, CTA +
  4 булевых флага + ai_tools + summary), нормализация значений вне списка в FALLBACK, 4 параллельных потока, остановка при
  отсутствии ключа или лимите.
- **Метрики** (`analytics/metrics.py`): везде медианы; ER по просмотрам; матрица «тема × роль»; топ постов.
- **Аудит** (`audit/content_audit.py`): сбор сайта (+5 страниц), Telegram за 60 дней, Instagram → разметка 25 постов →
  метрики канала → медиана рынка → модель ставит 0–10 по 5 критериям, общий балл — взвешенная сумма в коде. Запуск в потоке.
- **Контент Завод** (`factory.py`): форматы telegram/email/instagram с правилами длины, источники тем — идеи аудитов,
  идеи дайджеста, пустые клетки «тема × роль».
- **Задачи**: `jobs._run` — lock на имя задачи, запись в `runs`. Нет очереди, повторов и статусов этапов.
