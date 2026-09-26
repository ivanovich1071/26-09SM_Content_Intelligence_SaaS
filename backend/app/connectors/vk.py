"""VK: публичные сообщества через API (groups.getById + wall.get). Нужен сервисный ключ приложения VK
(`VK_SERVICE_TOKEN`) — один на сервер. Без ключа источник помечается недоступным с объяснением.
Токен уходит в теле POST-запроса, а не в URL: адреса запросов попадают в логи."""
import re
from datetime import UTC, datetime

from app.connectors.base import CollectResult, ContentItem, InvalidSource, SourceConnector, SourceProfile
from app.connectors.http import Fetcher, FetchError
from app.core.config import settings

API = "https://api.vk.com/method/{}"
API_VERSION = "5.199"
URL_RE = re.compile(r"^(?:https?://)?(?:www\.|m\.)?vk\.(?:com|ru)/([A-Za-z0-9_.]+)", re.I)
NOT_COMMUNITIES = {"feed", "im", "wall", "video", "photo", "music", "search", "away.php"}
NO_TOKEN = "VK собирается через API: администратор сервиса должен задать VK_SERVICE_TOKEN"
PAGE = 100


def _media_type(post: dict) -> str:
    types = [a.get("type") for a in post.get("attachments") or []]
    if "video" in types or "clip" in types:
        return "video"
    if types.count("photo") > 1:
        return "album"
    if "photo" in types:
        return "photo"
    if "poll" in types:
        return "poll"
    if "doc" in types:
        return "document"
    return "text"


def to_item(post: dict, screen_name: str) -> ContentItem:
    count = lambda k: (post.get(k) or {}).get("count")  # noqa: E731
    links = [a["link"]["url"] for a in post.get("attachments") or [] if a.get("type") == "link" and a.get("link")]
    return ContentItem(
        external_id=str(post["id"]), url=f"https://vk.com/wall{post['owner_id']}_{post['id']}",
        text=post.get("text") or "",
        published_at=datetime.fromtimestamp(post["date"], UTC) if post.get("date") else None,
        author=screen_name, media_type=_media_type(post), views=count("views"), likes=count("likes"),
        comments=count("comments"), shares=count("reposts"), links=links,
        raw_payload={"is_pinned": bool(post.get("is_pinned")), "marked_as_ads": bool(post.get("marked_as_ads"))},
    )


class VKConnector(SourceConnector):
    kind = "vk"

    def normalize(self, raw: str) -> tuple[str, str]:
        raw = raw.strip()
        m = URL_RE.match(raw)
        name = (m.group(1) if m else raw.removeprefix("@")).lower()
        if name in NOT_COMMUNITIES or not re.fullmatch(r"[a-z0-9_.]{2,64}", name):
            raise InvalidSource(f"Некорректное сообщество VK: {raw} — нужна ссылка vk.com/<сообщество>")
        return name, f"https://vk.com/{name}"

    async def _call(self, http: Fetcher, method: str, **params) -> dict | list:
        resp = await http.post(API.format(method), data={**params, "v": API_VERSION,
                                                          "access_token": settings.vk_service_token})
        if resp.status_code != 200:
            raise FetchError(f"VK API ответил HTTP {resp.status_code}")
        payload = resp.json()
        if "error" in payload:
            err = payload["error"]
            if err.get("error_code") in (5, 28):
                raise FetchError("VK отклонил VK_SERVICE_TOKEN — проверьте ключ")
            raise FetchError(f"VK: {err.get('error_msg', 'ошибка API')}")
        return payload["response"]

    async def get_profile(self, http: Fetcher, key: str, url: str, meta: dict) -> SourceProfile:
        if not settings.vk_service_token:
            return SourceProfile(available=False, reason=NO_TOKEN)
        try:
            resp = await self._call(http, "groups.getById", group_id=key, fields="members_count,description")
        except FetchError as e:
            if "ключ" in str(e):
                raise
            return SourceProfile(available=False, reason="Сообщество VK не найдено — личные страницы не поддерживаются")
        groups = resp.get("groups", []) if isinstance(resp, dict) else resp
        if not groups:
            return SourceProfile(available=False, reason="Сообщество VK не найдено")
        g = groups[0]
        if g.get("is_closed"):
            return SourceProfile(available=False, title=g.get("name"), reason="Закрытое сообщество VK")
        return SourceProfile(available=True, title=g.get("name"), description=g.get("description"),
                             followers=g.get("members_count"), meta={"owner_id": -int(g["id"])})

    async def collect(self, http: Fetcher, key: str, url: str, meta: dict, *, since: datetime,
                      known_ids: set[str]) -> CollectResult:
        items: list[ContentItem] = []
        pages = 0
        for offset in range(0, 5 * PAGE, PAGE):
            resp = await self._call(http, "wall.get", domain=key, count=PAGE, offset=offset)
            pages += 1
            posts = resp.get("items", [])
            page = [to_item(p, key) for p in posts]
            items.extend(page)
            regular = [i for i, p in zip(page, posts, strict=True) if not p.get("is_pinned")]
            oldest = min((i.published_at for i in regular if i.published_at), default=None)
            reached_known = bool(known_ids) and all(i.external_id in known_ids for i in regular[:5])
            if len(posts) < PAGE or (oldest and oldest < since) or reached_known:
                break
        fresh = [i for i in items if i.published_at is None or i.published_at >= since]
        return CollectResult(items=list({i.external_id: i for i in fresh}.values()), pages=pages)
