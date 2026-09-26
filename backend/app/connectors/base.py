"""Контракт коннектора источника. Всё платформенное остаётся в коннекторе и в raw_payload;
дальше по пайплайну идут только ContentItem."""
import hashlib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.connectors.http import Fetcher

TRACKING_PARAMS = re.compile(r"^(utm_\w+|fbclid|gclid|yclid|mc_cid|mc_eid|ref|_hsenc|_hsmi)$", re.I)


class InvalidSource(ValueError):
    """Адрес не подходит коннектору. Сообщение показывается пользователю."""


@dataclass
class SourceProfile:
    available: bool
    title: str | None = None
    description: str | None = None
    followers: int | None = None
    reason: str | None = None  # почему недоступен
    meta: dict = field(default_factory=dict)


@dataclass
class ContentItem:
    external_id: str
    url: str | None
    text: str
    published_at: datetime | None
    title: str | None = None
    author: str | None = None
    media_type: str = "text"
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    links: list[str] = field(default_factory=list)
    raw_payload: dict = field(default_factory=dict)

    @property
    def canonical_url(self) -> str | None:
        return canonical_url(self.url) if self.url else None

    @property
    def content_hash(self) -> str:
        return content_hash(" ".join(filter(None, [self.title, self.text])))


@dataclass
class CollectResult:
    items: list[ContentItem]
    pages: int = 0


class SourceConnector(ABC):
    kind: str

    @abstractmethod
    def normalize(self, raw: str) -> tuple[str, str]:
        """Адрес от пользователя → (ключ источника, URL). Без сети; InvalidSource при неверном вводе."""

    @abstractmethod
    async def get_profile(self, http: Fetcher, key: str, url: str, meta: dict) -> SourceProfile: ...

    @abstractmethod
    async def collect(self, http: Fetcher, key: str, url: str, meta: dict, *, since: datetime,
                      known_ids: set[str]) -> CollectResult:
        """Новые и обновлённые публикации не старше `since`. Останавливается, дойдя до уже известных."""

    async def health_check(self, http: Fetcher, key: str, url: str, meta: dict) -> SourceProfile:
        return await self.get_profile(http, key, url, meta)


def canonical_url(url: str) -> str:
    """Для дедупликации между источниками: без схемы-www-якоря-трекинга, хост в нижнем регистре."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower().removeprefix("www.")
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if not TRACKING_PARAMS.match(k)))
    return urlunsplit(("https", host, parts.path.rstrip("/") or "/", query, ""))


def content_hash(text: str) -> str:
    norm = re.sub(r"\s+", " ", text).strip().lower()
    return hashlib.sha256(norm.encode()).hexdigest()


def normalize_web_url(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        raise InvalidSource("Укажите адрес")
    if not re.match(r"^https?://", raw, re.I):
        raw = f"https://{raw}"
    parts = urlsplit(raw)
    if not parts.hostname or "." not in parts.hostname:
        raise InvalidSource(f"Некорректный адрес: {raw}")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))
