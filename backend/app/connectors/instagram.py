"""Instagram: публичные профили через Apify (apify/instagram-profile-scraper). Перенос из VM_SM.

Без входа в аккаунт Instagram не отдаёт посты, поэтому сбор идёт через актор Apify; ключ — `APIFY_TOKEN` на сервере,
один на всех клиентов. Профиль с ~12 последними постами — одна запись датасета (бесплатный кредит Apify $5/мес)."""
import re
from datetime import datetime

from app.connectors.base import CollectResult, ContentItem, InvalidSource, SourceConnector, SourceProfile
from app.connectors.http import Fetcher, FetchError
from app.core.config import settings

APIFY_ACTOR = "apify~instagram-profile-scraper"
APIFY_URL = f"https://api.apify.com/v2/acts/{APIFY_ACTOR}/run-sync-get-dataset-items"
FORMAT_MAP = {"image": "photo", "sidecar": "carousel", "video": "video"}
HANDLE_RE = re.compile(r"^[a-z0-9._]{1,30}$")
URL_RE = re.compile(r"^(?:https?://)?(?:www\.)?instagram\.com/([^/?#]+)", re.I)
NOT_PROFILES = {"p", "reel", "reels", "tv", "stories", "explore", "accounts"}
NO_TOKEN = "Instagram собирается через Apify: администратор сервиса должен задать APIFY_TOKEN"


def map_profile(item: dict) -> tuple[SourceProfile, list[ContentItem]]:
    """Запись датасета актора → профиль и посты."""
    profile = SourceProfile(
        available=True, title=item.get("fullName") or item.get("username"), description=item.get("biography"),
        followers=item.get("followersCount"), meta={"posts_total": item.get("postsCount")},
    )
    items = []
    for p in item.get("latestPosts") or []:
        fmt = FORMAT_MAP.get((p.get("type") or "").lower(), "photo")
        if p.get("productType") == "clips":
            fmt = "reel"
        likes = p.get("likesCount")
        code = p.get("shortCode") or str(p.get("id"))
        ts = p.get("timestamp")
        items.append(ContentItem(
            external_id=code, url=p.get("url") or f"https://www.instagram.com/p/{code}/",
            text=p.get("caption") or "", published_at=datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None,
            author=item.get("username"), media_type=fmt,
            views=p.get("videoPlayCount") or p.get("videoViewCount"),
            likes=likes if likes is not None and likes >= 0 else None,  # -1 — лайки скрыты автором
            comments=p.get("commentsCount"),
            raw_payload={"hashtags": p.get("hashtags") or [], "product_type": p.get("productType")},
        ))
    return profile, items


class InstagramConnector(SourceConnector):
    kind = "instagram"

    def normalize(self, raw: str) -> tuple[str, str]:
        raw = raw.strip()
        m = URL_RE.match(raw)
        handle = (m.group(1) if m else raw.removeprefix("@")).lower()
        if handle in NOT_PROFILES or not HANDLE_RE.match(handle):
            raise InvalidSource(f"Некорректный Instagram-профиль: {raw} — нужна ссылка на профиль или @имя")
        return handle, f"https://www.instagram.com/{handle}/"

    async def _item(self, http: Fetcher, key: str) -> dict | None:
        """Один вызов актора на задачу: профиль и посты берутся из одного ответа."""
        cache_key = ("instagram", key)
        if cache_key not in http.cache:
            resp = await http.post(APIFY_URL, json={"usernames": [key]},
                                   headers={"Authorization": f"Bearer {settings.apify_token}"},
                                   timeout=settings.apify_timeout_sec)
            if resp.status_code in (401, 403):
                raise FetchError("Apify отклонил APIFY_TOKEN — проверьте ключ")
            if resp.status_code == 402:
                raise FetchError("Исчерпан кредит Apify")
            if resp.status_code >= 400:
                raise FetchError(f"Apify ответил HTTP {resp.status_code}")
            data = resp.json()
            match = [i for i in data if (i.get("username") or "").lower() == key] if isinstance(data, list) else []
            http.cache[cache_key] = match[0] if match else None
        return http.cache[cache_key]

    async def get_profile(self, http: Fetcher, key: str, url: str, meta: dict) -> SourceProfile:
        if not settings.apify_token:
            return SourceProfile(available=False, reason=NO_TOKEN)
        item = await self._item(http, key)
        if item is None or item.get("error"):
            return SourceProfile(available=False, reason="Профиль не найден или закрыт")
        return map_profile(item)[0]

    async def collect(self, http: Fetcher, key: str, url: str, meta: dict, *, since: datetime,
                      known_ids: set[str]) -> CollectResult:
        item = await self._item(http, key)
        items = map_profile(item)[1] if item else []
        return CollectResult(items=[i for i in items if i.published_at is None or i.published_at >= since], pages=1)
