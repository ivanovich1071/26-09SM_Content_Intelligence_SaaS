"""RSS 2.0 и Atom. defusedxml — лента приходит с чужого сервера."""
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from xml.etree.ElementTree import Element

from defusedxml import ElementTree
from selectolax.parser import HTMLParser

from app.connectors.base import (
    CollectResult,
    ContentItem,
    SourceConnector,
    SourceProfile,
    normalize_web_url,
)
from app.connectors.http import Fetcher

ATOM = "{http://www.w3.org/2005/Atom}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"
DC_CREATOR = "{http://purl.org/dc/elements/1.1/}creator"


class FeedError(ValueError):
    pass


def html_to_text(html: str | None) -> str:
    if not html:
        return ""
    if "<" not in html:
        return html.strip()
    tree = HTMLParser(re.sub(r"<br\s*/?>|</p>", "\n", html, flags=re.I))
    for tag in tree.css("script, style"):
        tag.decompose()
    text = tree.text(separator="") if tree.body is None else tree.body.text(separator="")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)  # дата без пояса — считаем UTC


def _text(el: Element | None, tag: str) -> str | None:
    node = el.find(tag) if el is not None else None
    return node.text.strip() if node is not None and node.text else None


def parse_feed(xml: bytes | str) -> tuple[dict, list[ContentItem]]:
    """→ (заголовок/описание ленты, публикации)."""
    try:
        root = ElementTree.fromstring(xml)
    except Exception as e:  # noqa: BLE001 — ParseError, DTDForbidden, EntitiesForbidden
        raise FeedError(f"Не RSS/Atom: {type(e).__name__}") from e
    if root.tag == f"{ATOM}feed":
        return _parse_atom(root)
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise FeedError("Не RSS/Atom: неизвестный корневой элемент")
    feed = {"title": _text(channel, "title"), "description": _text(channel, "description")}
    items = []
    for it in channel.findall("item"):
        link = _text(it, "link")
        guid = _text(it, "guid") or link or _text(it, "title")
        if not guid:
            continue
        body = _text(it, CONTENT) or _text(it, "description")
        items.append(ContentItem(
            external_id=guid[:500], url=link, title=_text(it, "title"), text=html_to_text(body),
            published_at=_parse_date(_text(it, "pubDate")), author=_text(it, DC_CREATOR) or _text(it, "author"),
            raw_payload={"categories": [c.text for c in it.findall("category") if c.text]},
        ))
    return feed, items


def _parse_atom(root: Element) -> tuple[dict, list[ContentItem]]:
    feed = {"title": _text(root, f"{ATOM}title"), "description": _text(root, f"{ATOM}subtitle")}
    items = []
    for entry in root.findall(f"{ATOM}entry"):
        link = None
        for ln in entry.findall(f"{ATOM}link"):
            if ln.get("rel", "alternate") == "alternate":
                link = ln.get("href")
                break
        guid = _text(entry, f"{ATOM}id") or link
        if not guid:
            continue
        body = _text(entry, f"{ATOM}content") or _text(entry, f"{ATOM}summary")
        author = entry.find(f"{ATOM}author")
        items.append(ContentItem(
            external_id=guid[:500], url=link, title=_text(entry, f"{ATOM}title"), text=html_to_text(body),
            published_at=_parse_date(_text(entry, f"{ATOM}published") or _text(entry, f"{ATOM}updated")),
            author=_text(author, f"{ATOM}name"),
            raw_payload={"categories": [c.get("term") for c in entry.findall(f"{ATOM}category") if c.get("term")]},
        ))
    return feed, items


class RSSConnector(SourceConnector):
    kind = "rss"

    def normalize(self, raw: str) -> tuple[str, str]:
        url = normalize_web_url(raw)
        return url, url

    async def _fetch(self, http: Fetcher, url: str) -> tuple[dict, list[ContentItem]]:
        resp = await http.get(url)
        if resp.status_code != 200:
            raise FeedError(f"Лента ответила HTTP {resp.status_code}")
        return parse_feed(resp.content)

    async def get_profile(self, http: Fetcher, key: str, url: str, meta: dict) -> SourceProfile:
        try:
            feed, _ = await self._fetch(http, url)
        except FeedError as e:
            return SourceProfile(available=False, reason=str(e))
        return SourceProfile(available=True, title=feed["title"], description=feed["description"])

    async def collect(self, http: Fetcher, key: str, url: str, meta: dict, *, since: datetime,
                      known_ids: set[str]) -> CollectResult:
        _, items = await self._fetch(http, url)
        return CollectResult(items=[i for i in items if i.published_at is None or i.published_at >= since], pages=1)
