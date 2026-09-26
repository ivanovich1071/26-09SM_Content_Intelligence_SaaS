"""YouTube без ключа: публичная Atom-лента канала youtube.com/feeds/videos.xml?channel_id=… (15 последних видео
с просмотрами и лайками). Для адреса вида @handle channel_id берётся со страницы канала один раз и хранится в meta."""
import re
from datetime import datetime
from xml.etree.ElementTree import Element

from defusedxml import ElementTree

from app.connectors.base import CollectResult, ContentItem, InvalidSource, SourceConnector, SourceProfile
from app.connectors.http import Fetcher
from app.connectors.rss import FeedError

ATOM = "{http://www.w3.org/2005/Atom}"
YT = "{http://www.youtube.com/xml/schemas/2015}"
MEDIA = "{http://search.yahoo.com/mrss/}"
FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
URL_RE = re.compile(r"^(?:https?://)?(?:www\.|m\.)?youtube\.com/(@[\w.\-]+|channel/UC[\w-]{22}|c/[\w.\-]+|user/[\w.\-]+)",
                    re.I)
CHANNEL_ID_RE = re.compile(r'"(?:channelId|externalId|browseId)":"(UC[\w-]{22})"|channel/(UC[\w-]{22})')
# Без согласия на cookies европейские запросы уходят на consent.youtube.com
CONSENT = {"Cookie": "CONSENT=YES+cb; SOCS=CAI", "Accept-Language": "ru,en;q=0.8"}


def _int(value: str | None) -> int | None:
    return int(value) if value and value.isdigit() else None


def parse_feed(xml: bytes | str) -> tuple[str | None, list[ContentItem]]:
    try:
        root = ElementTree.fromstring(xml)
    except Exception as e:  # noqa: BLE001
        raise FeedError(f"Лента YouTube не разобрана: {type(e).__name__}") from e
    if root.tag != f"{ATOM}feed":
        raise FeedError("Это не лента YouTube")
    title_el = root.find(f"{ATOM}title")
    items = []
    for entry in root.findall(f"{ATOM}entry"):
        video_id = (entry.findtext(f"{YT}videoId") or "").strip()
        if not video_id:
            continue
        group: Element | None = entry.find(f"{MEDIA}group")
        stats = group.find(f"{MEDIA}community/{MEDIA}statistics") if group is not None else None
        rating = group.find(f"{MEDIA}community/{MEDIA}starRating") if group is not None else None
        published = entry.findtext(f"{ATOM}published")
        link = entry.find(f"{ATOM}link")
        items.append(ContentItem(
            external_id=video_id, url=link.get("href") if link is not None else f"https://www.youtube.com/watch?v={video_id}",
            title=(entry.findtext(f"{ATOM}title") or "").strip() or None,
            text=(group.findtext(f"{MEDIA}description") if group is not None else "") or "",
            published_at=datetime.fromisoformat(published) if published else None,
            author=(entry.findtext(f"{ATOM}author/{ATOM}name") or "").strip() or None, media_type="video",
            views=_int(stats.get("views")) if stats is not None else None,
            likes=_int(rating.get("count")) if rating is not None else None,
        ))
    return (title_el.text.strip() if title_el is not None and title_el.text else None), items


class YouTubeConnector(SourceConnector):
    kind = "youtube"

    def normalize(self, raw: str) -> tuple[str, str]:
        m = URL_RE.match(raw.strip())
        if not m:
            raise InvalidSource("Нужна ссылка на канал YouTube: youtube.com/@канал или youtube.com/channel/UC…")
        path = m.group(1)
        key = path.split("/", 1)[1] if path.lower().startswith("channel/") else path.lower()
        return key, f"https://www.youtube.com/{path}"

    async def _channel_id(self, http: Fetcher, key: str, url: str, meta: dict) -> str | None:
        if key.startswith("UC"):
            return key
        if meta.get("channel_id"):
            return meta["channel_id"]
        resp = await http.get(url, headers=CONSENT)
        if resp.status_code != 200:
            return None
        m = CHANNEL_ID_RE.search(resp.text)
        return (m.group(1) or m.group(2)) if m else None

    async def get_profile(self, http: Fetcher, key: str, url: str, meta: dict) -> SourceProfile:
        channel_id = await self._channel_id(http, key, url, meta)
        if not channel_id:
            return SourceProfile(available=False, reason="Канал YouTube не найден — проверьте ссылку")
        resp = await http.get(FEED_URL.format(channel_id))
        if resp.status_code != 200:
            return SourceProfile(available=False, reason=f"Лента канала ответила HTTP {resp.status_code}")
        title, items = parse_feed(resp.content)
        http.cache[("youtube", channel_id)] = items
        return SourceProfile(available=True, title=title, meta={"channel_id": channel_id})

    async def collect(self, http: Fetcher, key: str, url: str, meta: dict, *, since: datetime,
                      known_ids: set[str]) -> CollectResult:
        channel_id = meta.get("channel_id") or key
        items = http.cache.get(("youtube", channel_id))
        if items is None:
            resp = await http.get(FEED_URL.format(channel_id))
            if resp.status_code != 200:
                raise FeedError(f"Лента канала ответила HTTP {resp.status_code}")
            items = parse_feed(resp.content)[1]
        return CollectResult(items=[i for i in items if i.published_at is None or i.published_at >= since], pages=1)
