"""Запуск: `arq app.workers.settings.WorkerSettings`."""
from arq.connections import RedisSettings

from app.core.config import settings
from app.workers import tasks


class WorkerSettings:
    functions = [tasks.ping]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 10
    job_timeout = 60 * 30  # аудит и генерации долгие; фронт не ставит коротких таймаутов
