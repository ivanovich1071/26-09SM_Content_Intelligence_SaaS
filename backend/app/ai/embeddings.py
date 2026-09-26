"""Абстракция эмбеддингов. Проект не привязан к OpenAI: провайдер и модель — в конфиге."""
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from app.core.config import settings

# Запасная цена за 1M входных токенов, если провайдер не вернул стоимость
FALLBACK_PRICE_PER_M = 0.02


class EmbeddingError(RuntimeError):
    pass


@dataclass
class EmbedResult:
    vectors: list[list[float]]
    model: str
    usage: dict = field(default_factory=dict)

    @property
    def cost_usd(self) -> float:
        if isinstance(self.usage.get("cost"), int | float):
            return float(self.usage["cost"])
        return (self.usage.get("prompt_tokens") or self.usage.get("total_tokens") or 0) * FALLBACK_PRICE_PER_M / 1e6


class EmbeddingProvider(Protocol):
    name: str
    model: str
    dim: int

    async def embed(self, texts: list[str]) -> EmbedResult: ...


class OpenAICompatibleEmbeddings:
    """OpenRouter / OpenAI / любой сервис с эндпоинтом /embeddings."""
    name = "openrouter"

    def __init__(self, api_key: str | None = None, base_url: str | None = None, model: str | None = None,
                 dim: int | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self.api_key = settings.openrouter_api_key if api_key is None else api_key
        self.base_url = base_url or settings.openrouter_base_url
        self.model = model or settings.embedding_model
        self.dim = dim or settings.embedding_dim
        self._transport = transport

    async def embed(self, texts: list[str]) -> EmbedResult:
        if not self.api_key:
            raise EmbeddingError("Не задан OPENROUTER_API_KEY")
        try:
            async with httpx.AsyncClient(timeout=120, transport=self._transport) as client:
                resp = await client.post(f"{self.base_url}/embeddings",
                                         json={"model": self.model, "input": texts, "dimensions": self.dim},
                                         headers={"Authorization": f"Bearer {self.api_key}"})
        except httpx.HTTPError as e:
            raise EmbeddingError(f"сеть: {type(e).__name__}") from e
        if resp.status_code >= 400:
            raise EmbeddingError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        payload = resp.json()
        vectors = [item["embedding"] for item in sorted(payload["data"], key=lambda d: d["index"])]
        if len(vectors) != len(texts) or any(len(v) != self.dim for v in vectors):
            raise EmbeddingError(f"ответ не совпал с запросом: {len(vectors)} векторов, нужна размерность {self.dim}")
        return EmbedResult(vectors=vectors, model=self.model, usage=payload.get("usage") or {})


def get_embedding_provider() -> EmbeddingProvider:
    return OpenAICompatibleEmbeddings()
