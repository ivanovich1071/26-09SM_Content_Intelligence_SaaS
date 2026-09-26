"""Настройки из окружения. Model ID и ключи задаются только здесь — в модулях их не пишем."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    app_name: str = "SM Content Intelligence"
    environment: str = "dev"
    secret_key: str = "change-me"
    access_token_minutes: int = 30
    refresh_token_days: int = 30
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    database_url: str = "postgresql+asyncpg://sm:sm@localhost:5432/sm"
    redis_url: str = "redis://localhost:6379/0"

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Модели по задачам AI Router. ID сверять на https://openrouter.ai/models
    llm_model_analyze: str = "qwen/qwen3.8-max"
    llm_model_classify: str = "qwen/qwen3.8-flash"
    llm_model_write: str = "qwen/qwen3.8-max"
    llm_model_qa: str = "qwen/qwen3.8-flash"
    llm_timeout_sec: float = 180
    llm_retries: int = 3

    embedding_provider: str = "openrouter"
    embedding_model: str = "openai/text-embedding-3-small"
    embedding_dim: int = 1536

    # Сбор источников
    user_agent: str = "Mozilla/5.0 (compatible; SMContentIntelligence/0.1; +https://github.com/ivanovich1071)"
    source_history_days: int = 90         # глубина первого сбора
    source_fresh_minutes: int = 30        # источник, собранный недавно (другим клиентом), не собираем повторно
    domain_min_interval_sec: float = 1.5  # пауза между запросами к одному домену — общая для всех воркеров
    telegram_max_pages: int = 15          # ~20 постов на странице t.me/s
    fetch_max_bytes: int = 5_000_000


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
