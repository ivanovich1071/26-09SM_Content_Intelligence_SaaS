"""Публичные Telegram-каналы через веб-зеркало t.me/s/<канал> — без аккаунта и ключей. Перенос из VM_SM.

t.me/<handle>   — карточка: название, описание, точное число подписчиков.
t.me/s/<handle> — лента: ~20 последних постов, дальше листается через ?before=<id>.
Для личных аккаунтов, групп и ботов карточка не содержит «subscribers» — такие хэндлы недоступны."""
import re
from datetime import datetime
from urllib.parse import unquote

from selectolax.parser import HTMLParser, Node

from app.connectors.base import CollectResult, ContentItem, InvalidSource, SourceConnector, SourceProfile
from app.connectors.http import Fetcher
from app.core.config import settings

HANDLE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
URL_RE = re.compile(r"^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/(?:s/)?([^/?#]+)", re.I)


def parse_count(s: str | None) -> int | None:
    """'1.39K' → 1390, '2M' → 2000000, '1 394' → 1394."""
    if not s:
        return None
    s = s.strip().replace("\xa0", " ").replace(" ", "").replace(",", ".")
    m = re.fullmatch(r"([\d.]+)([KkMm]?)", s)
    if not m:
        return None
    try:
        num = float(m.group(1))
    except ValueError:
        return None
    mult = {"k": 1_000, "m": 1_000_000}.get(m.group(2).lower(), 1)
    return int(round(num * mult))


def parse_channel_page(html: str) -> dict:
    tree = HTMLParser(html)
    title = tree.css_first(".tgme_page_title")
    desc = tree.css_first(".tgme_page_description")
    extra = tree.css_first(".tgme_page_extra")
    extra_text = extra.text(strip=True) if extra else ""
    m = re.search(r"([\d\s\xa0]+)\s+(subscribers|members)", extra_text)
    return {
        "title": title.text(strip=True) if title else None,
        "description": desc.text(separator="\n", strip=True) if desc else None,
        "followers": int(re.sub(r"\D", "", m.group(1))) if m else None,
        "is_channel": bool(m and m.group(2) == "subscribers"),
    }


def _text_with_breaks(node: Node) -> str:
    html = re.sub(r"<br\s*/?>", "\n", node.html or "")
    return HTMLParser(html).text(separator="").strip()


def _post_format(msg: Node) -> str:
    if msg.css_first(".tgme_widget_message_video_player, .tgme_widget_message_roundvideo_player"):
        return "album" if msg.css_first(".tgme_widget_message_grouped_wrap") else "video"
    if msg.css_first(".tgme_widget_message_grouped_wrap"):
        return "album"
    if msg.css_first(".tgme_widget_message_photo_wrap"):
        return "photo"
    if msg.css_first(".tgme_widget_message_document"):
        return "document"
    if msg.css_first(".tgme_widget_message_poll"):
        return "poll"
    return "text"


def _external_links(text_node: Node | None, handle: str) -> list[str]:
    if not text_node:
        return []
    out = []
    for a in text_node.css("a[href]"):
        href = a.attributes.get("href") or ""
        if not href.startswith("http") or "?q=%23" in href:  # хэштеги
            continue
        if re.match(rf"https?://t\.me/{re.escape(handle)}(/|$)", href, re.I):
            continue
        out.append(unquote(href))
    return list(dict.fromkeys(out))


def parse_feed(html: str, handle: str) -> tuple[list[dict], int | None]:
    """Посты страницы и курсор для следующей (более старой) страницы."""
    tree = HTMLParser(html)
    posts = []
    for msg in tree.css(".tgme_widget_message[data-post]"):
        if "service_message" in (msg.attributes.get("class") or ""):
            continue
        data_post = msg.attributes["data-post"]
        ext_id = data_post.split("/")[-1]
        text_node = msg.css_first(".tgme_widget_message_bubble > .tgme_widget_message_text") \
            or msg.css_first(".tgme_widget_message_text")
        views = msg.css_first(".tgme_widget_message_views")
        time_node = msg.css_first(".tgme_widget_message_date time")
        reactions = 0
        for r in msg.css(".tgme_reaction"):
            reactions += parse_count(re.sub(r"[^\d.KkMm]", "", r.text(strip=True))) or 0
        posts.append({
            "external_id": ext_id,
            "url": f"https://t.me/{data_post}",
            "published_at": time_node.attributes.get("datetime") if time_node else None,
            "text": _text_with_breaks(text_node) if text_node else "",
            "format": _post_format(msg),
            "views": parse_count(views.text(strip=True)) if views else None,
            "likes": reactions,
            "links": _external_links(text_node, handle),
            "forwarded": bool(msg.css_first(".tgme_widget_message_forwarded_from")),
        })
    more = tree.css_first(".tme_messages_more[data-before]")
    before = int(more.attributes["data-before"]) if more else None
    return posts, before


def _to_item(p: dict, handle: str) -> ContentItem:
    published = datetime.fromisoformat(p["published_at"]) if p["published_at"] else None
    return ContentItem(
        external_id=p["external_id"], url=p["url"], text=p["text"], published_at=published, author=handle,
        media_type=p["format"], views=p["views"], likes=p["likes"], links=p["links"],
        raw_payload={"forwarded": p["forwarded"]},
    )


class TelegramConnector(SourceConnector):
    kind = "telegram"

    def normalize(self, raw: str) -> tuple[str, str]:
        raw = raw.strip()
        m = URL_RE.match(raw)
        handle = m.group(1) if m else raw.removeprefix("@")
        if handle.startswith("+") or handle.lower() == "joinchat":
            raise InvalidSource("Закрытые каналы по ссылке-приглашению не поддерживаются — нужен публичный @канал")
        if not HANDLE_RE.match(handle):
            raise InvalidSource(f"Некорректный Telegram-канал: {raw}")
        return handle.lower(), f"https://t.me/{handle}"

    async def get_profile(self, http: Fetcher, key: str, url: str, meta: dict) -> SourceProfile:
        resp = await http.get(f"https://t.me/{key}")
        if resp.status_code != 200:
            return SourceProfile(available=False, reason=f"t.me ответил HTTP {resp.status_code}")
        info = parse_channel_page(resp.text)
        if not info["is_channel"]:
            return SourceProfile(available=False, title=info["title"],
                                 reason="Это не публичный канал (личный аккаунт, группа или бот)")
        return SourceProfile(available=True, title=info["title"], description=info["description"],
                             followers=info["followers"])

    async def collect(self, http: Fetcher, key: str, url: str, meta: dict, *, since: datetime,
                      known_ids: set[str]) -> CollectResult:
        items: list[ContentItem] = []
        page_url, pages = f"https://t.me/s/{key}", 0
        for _ in range(settings.telegram_max_pages):
            resp = await http.get(page_url, follow_redirects=False)
            pages += 1
            if resp.status_code != 200:
                break
            page, before = parse_feed(resp.text, key)
            items.extend(_to_item(p, key) for p in page)
            dates = [i.published_at for i in items[-len(page):] if i.published_at] if page else []
            oldest = min(dates) if dates else None
            reached_known = bool(known_ids) and all(p["external_id"] in known_ids for p in page[:5])
            if not before or not page or (oldest and oldest < since) or reached_known:
                break
            page_url = f"https://t.me/s/{key}?before={before}"
        fresh = [i for i in items if i.published_at is None or i.published_at >= since]
        return CollectResult(items=list({i.external_id: i for i in fresh}.values()), pages=pages)
