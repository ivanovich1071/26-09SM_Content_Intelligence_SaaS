"""Коннекторы источников. Новый тип: класс SourceConnector + запись в CONNECTORS + значение в SourceKind."""
import re

from app.connectors.base import InvalidSource, SourceConnector
from app.connectors.instagram import InstagramConnector
from app.connectors.rss import RSSConnector
from app.connectors.telegram import TelegramConnector
from app.connectors.vk import VKConnector
from app.connectors.website import WebsiteConnector
from app.connectors.youtube import YouTubeConnector
from app.core.config import settings

CONNECTORS: dict[str, SourceConnector] = {
    c.kind: c for c in (TelegramConnector(), WebsiteConnector(), RSSConnector(), InstagramConnector(),
                           YouTubeConnector(), VKConnector())
}

FEED_HINT = re.compile(r"(/feed|/rss|/atom|\.rss|\.xml)(/|$|\?)", re.I)


def get_connector(kind: str) -> SourceConnector:
    return CONNECTORS[kind]


KEYS_NEEDED = {"instagram": "apify_token", "vk": "vk_service_token"}  # тип → настройка с ключом


def missing_key(kind: str | None) -> bool:
    need = KEYS_NEEDED.get(kind or "")
    return bool(need and not getattr(settings, need))


SOCIAL_DOMAINS = {
    "telegram": r"(t|telegram)\.me", "instagram": r"instagram\.com", "youtube": r"(m\.)?youtube\.com",
    "vk": r"(m\.)?vk\.(com|ru)",
}


def social_kind(url: str) -> str | None:
    """Поддерживаемая соцсеть по ссылке или None (сайт, лента, неподдерживаемая сеть)."""
    for kind, domain in SOCIAL_DOMAINS.items():
        if re.match(rf"^(https?://)?(www\.)?{domain}/", url.strip(), re.I):
            return kind
    return None


def detect_kind(raw: str) -> str:
    """Тип по адресу без сети: соцсети по домену, @канал → telegram, адрес ленты → rss, иначе сайт."""
    raw = raw.strip()
    kind = social_kind(raw)
    if kind:
        return kind
    if raw.startswith("@") or re.match(r"^(https?://)?(www\.)?(t|telegram)\.me/", raw, re.I):
        return "telegram"
    if FEED_HINT.search(raw):
        return "rss"
    return "website"


__all__ = ["CONNECTORS", "KEYS_NEEDED", "InvalidSource", "SourceConnector", "detect_kind", "get_connector",
           "missing_key", "social_kind"]
