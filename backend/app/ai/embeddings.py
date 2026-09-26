"""Абстракция эмбеддингов. Проект не привязан к OpenAI: провайдер и модель — в конфиге.
Используется с EPIC 3 (pgvector)."""
from typing import Protocol

import httpx

from app.core.config import settings


class EmbeddingProvider(Protocol):
    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAICompatibleEmbeddings:
    """OpenRouter / OpenAI / любой сервис с эндпоинтом /embeddings."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None, model: str | None = None,
                 dim: int | None = None):
        self.api_key = api_key or settings.openrouter_api_key
        self.base_url = base_url or settings.openrouter_base_url
        self.model = model or settings.embedding_model
        self.dim = dim or settings.embedding_dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(f"{self.base_url}/embeddings", json={"model": self.model, "input": texts},
                                     headers={"Authorization": f"Bearer {self.api_key}"})
            resp.raise_for_status()
        return [item["embedding"] for item in sorted(resp.json()["data"], key=lambda d: d["index"])]


def get_embedding_provider() -> EmbeddingProvider:
    return OpenAICompatibleEmbeddings()
