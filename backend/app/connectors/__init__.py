"""Коннекторы источников. Новый тип: класс SourceConnector + запись в CONNECTORS + значение в SourceKind."""
import re

from app.connectors.base import InvalidSource, SourceConnector
from app.connectors.rss import RSSConnector
from app.connectors.telegram import TelegramConnector
from app.connectors.website import WebsiteConnector

CONNECTORS: dict[str, SourceConnector] = {
    c.kind: c for c in (TelegramConnector(), WebsiteConnector(), RSSConnector())
}

FEED_HINT = re.compile(r"(/feed|/rss|/atom|\.rss|\.xml)(/|$|\?)", re.I)


def get_connector(kind: str) -> SourceConnector:
    return CONNECTORS[kind]


def detect_kind(raw: str) -> str:
    """Тип по адресу без сети: t.me и @канал → telegram, адрес ленты → rss, иначе сайт."""
    raw = raw.strip()
    if raw.startswith("@") or re.match(r"^(https?://)?(www\.)?(t|telegram)\.me/", raw, re.I):
        return "telegram"
    if FEED_HINT.search(raw):
        return "rss"
    return "website"


__all__ = ["CONNECTORS", "InvalidSource", "SourceConnector", "detect_kind", "get_connector"]
