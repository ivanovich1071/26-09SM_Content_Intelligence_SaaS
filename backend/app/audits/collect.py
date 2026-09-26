"""Сбор для аудита: какие каналы аудировать, их синхронизация (общими global_sources) и выдержка сайта.

Каналы компании не подключаются к организации как `sources` — аудит можно делать и для чужой компании
(потенциальный клиент агентства), не засоряя ленту и рынок."""
import re
from dataclasses import dataclass, field

from selectolax.parser import HTMLParser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis import metrics
from app.connectors import InvalidSource, missing_key, social_kind
from app.connectors.http import Fetcher, FetchError
from app.connectors.website import extract_social, extract_text, page_meta
from app.models import ContentAudit, GlobalSource, Source, SourceKind, SourceRole
from app.sources import service, sync
from app.websites.discover import discover

SITE_PAGES = 6              # главная + ключевые страницы (цены, услуги, о компании, блог, контакты)
SITE_EXCERPT_CHARS = 1500   # с каждой страницы — модели
AUTO_SOCIAL = 3             # сколько соцсетей с сайта подхватить, если каналы не указаны
CONTACT_RE = re.compile(r"(tel:|mailto:|wa\.me|api\.whatsapp|viber:|t\.me/)", re.I)


@dataclass
class Channel:
    gs: GlobalSource
    origin: str                      # input | own | site
    error: str | None = None
    sync: dict = field(default_factory=dict)


def resolve_inputs(urls: list[str]) -> list[tuple[SourceKind, str, str]]:
    """Проверка адресов при создании аудита: InvalidSource с понятным текстом."""
    return [service.resolve(u) for u in urls if u.strip()]


async def site_snapshot(http: Fetcher, url: str) -> dict:
    out: dict = {"url": url, "pages": [], "social_links": [], "forms": 0, "contacts": 0, "error": None}
    try:
        home = await http.get(url)
    except FetchError as e:
        out["error"] = f"Сайт не открылся: {e}"
        return out
    if home.status_code != 200:
        out["error"] = f"Сайт ответил HTTP {home.status_code}"
        return out
    meta = page_meta(home.text, url)
    out.update(title=meta["title"], description=meta["description"])
    social: set[str] = set()
    for page_url, kind in await discover(http, url, home.text, SITE_PAGES):
        if page_url.rstrip("/") == url.rstrip("/"):
            resp = home
        else:
            try:
                resp = await http.get(page_url)
            except FetchError:
                continue
            if resp.status_code != 200:
                continue
        tree = HTMLParser(resp.text)
        out["forms"] += len(tree.css("form"))
        out["contacts"] += sum(1 for a in tree.css("a[href]") if CONTACT_RE.match(a.attributes.get("href") or ""))
        social |= set(extract_social(resp.text))
        title = tree.css_first("title")
        out["pages"].append({"url": page_url, "kind": kind, "title": title.text(strip=True)[:200] if title else None,
                             "text": extract_text(resp.text)[:SITE_EXCERPT_CHARS]})
    out["social_links"] = sorted(social)
    return out


async def channels(session: AsyncSession, audit: ContentAudit, site: dict | None) -> list[Channel]:
    found: dict[int, Channel] = {}

    async def add(kind: SourceKind, key: str, url: str, origin: str) -> None:
        gs = await service.global_source(session, kind, key, url)
        found.setdefault(gs.id, Channel(gs, origin))

    for kind, key, url in resolve_inputs(audit.inputs or []):
        await add(kind, key, url, "input")
    if audit.website:
        kind, key, url = service.resolve(audit.website, SourceKind.website)
        await add(kind, key, url, "input")  # блог сайта через RSS, если он есть
    if audit.use_own_sources:
        own = (await session.execute(select(Source).where(
            Source.organization_id == audit.organization_id, Source.role == SourceRole.own,
            Source.enabled.is_(True)))).scalars()
        for s in own:
            found.setdefault(s.global_source_id, Channel(s.global_source, "own"))
    # «Определение источников»: каналы не указаны — берём соцсети, найденные на сайте
    if site and not any(c.gs.kind != SourceKind.website for c in found.values()):
        auto = [u for u in site.get("social_links", []) if social_kind(u) and not missing_key(social_kind(u))]
        for link in auto[:AUTO_SOCIAL]:
            try:
                await add(*service.resolve(link), "site")
            except InvalidSource:
                continue
    await session.commit()
    return list(found.values())


async def sync_channel(session: AsyncSession, http: Fetcher, ch: Channel) -> None:
    try:
        ch.sync = await sync.sync_global_source(session, ch.gs, http)
    except sync.SyncError as e:  # статус источника уже сохранён sync_global_source
        ch.error = str(e)
        return
    await metrics.calculate(session, ch.gs)
    await session.commit()
