"""Поиск страниц сайта: robots.txt → sitemap (с индексами) + ссылки с главной; только тот же хост.
Тип страницы — по адресу; важные (цены, услуги, о компании) отслеживаются первыми, затем свежие статьи."""
import re
from datetime import datetime
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from defusedxml import ElementTree
from selectolax.parser import HTMLParser

from app.connectors.http import Fetcher, FetchError
from app.core.config import settings

SM = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
MAX_SITEMAPS = 5
MAX_CANDIDATES = 2000
SKIP_EXT = re.compile(r"\.(jpe?g|png|gif|webp|svg|ico|pdf|docx?|xlsx?|pptx?|zip|rar|mp[34]|avi|mov|css|js|xml|json)$",
                      re.I)
SKIP_PATH = re.compile(r"/(tag|tags|author|page|feed|wp-json|wp-admin|cart|checkout|login|search|cdn-cgi)(/|$)|[?&]",
                       re.I)
KINDS = [  # порядок = приоритет при лимите страниц
    ("pricing", re.compile(r"price|pricing|tarif|тариф|цен[аы]|стоимост|prays|ceny", re.I)),
    ("service", re.compile(r"servic|uslug|услуг|product|solution|reshen|решени|kurs|course|obuchen|обучен|programm",
                           re.I)),
    ("about", re.compile(r"about|o-nas|o-kompanii|о-нас|company|komand|team", re.I)),
    ("contacts", re.compile(r"contact|kontakt|контакт", re.I)),
    ("article", re.compile(r"/(blog|news|novosti|articles?|stat[ьi]i?|media|insights|journal)/[^/]+|/20\d\d/", re.I)),
    ("blog", re.compile(r"/(blog|news|novosti|articles?|stat[ьi]i?|media|insights|journal)/?$", re.I)),
]
PRIORITY = {"home": 0, "pricing": 1, "service": 2, "about": 3, "blog": 4, "contacts": 5, "other": 6, "article": 7}


def normalize(url: str) -> str:
    p = urlsplit(url)
    path = re.sub(r"/{2,}", "/", p.path or "/")
    if len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), path, "", ""))


def classify(url: str, home: str) -> str:
    if normalize(url) == normalize(home):
        return "home"
    path = urlsplit(url).path
    for kind, rx in KINDS:
        if rx.search(path):
            return kind
    return "other"


def same_site(url: str, home: str) -> bool:
    a, b = urlsplit(url).netloc.lower().removeprefix("www."), urlsplit(home).netloc.lower().removeprefix("www.")
    return a == b and urlsplit(url).scheme in ("http", "https")


def links(html: str, base: str) -> list[str]:
    out = []
    for a in HTMLParser(html).css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if href and not href.startswith(("#", "mailto:", "tel:", "javascript:")):
            out.append(urljoin(base, href))
    return out


def parse_sitemap(xml: bytes) -> tuple[list[tuple[str, datetime | None]], list[str]]:
    """→ (страницы с lastmod, вложенные sitemap)."""
    try:
        root = ElementTree.fromstring(xml)
    except Exception:  # noqa: BLE001 — битый или не XML
        return [], []
    pages, nested = [], []
    for sm in root.findall(f"{SM}sitemap"):
        loc = (sm.findtext(f"{SM}loc") or "").strip()
        if loc:
            nested.append(loc)
    for u in root.findall(f"{SM}url"):
        loc = (u.findtext(f"{SM}loc") or "").strip()
        lastmod = (u.findtext(f"{SM}lastmod") or "").strip()
        try:
            dt = datetime.fromisoformat(lastmod.replace("Z", "+00:00")) if lastmod else None
        except ValueError:
            dt = None
        if loc:
            pages.append((loc, dt))
    return pages, nested


async def robots(http: Fetcher, home: str) -> tuple[RobotFileParser, list[str]]:
    rp = RobotFileParser()
    root = f"{urlsplit(home).scheme}://{urlsplit(home).netloc}"
    try:
        resp = await http.get(f"{root}/robots.txt")
        text = resp.text if resp.status_code == 200 else ""
    except FetchError:
        text = ""
    rp.parse(text.splitlines())
    sitemaps = [ln.split(":", 1)[1].strip() for ln in text.splitlines() if ln.lower().startswith("sitemap:")]
    return rp, sitemaps or [f"{root}/sitemap.xml"]


async def discover(http: Fetcher, home: str, home_html: str, limit: int) -> list[tuple[str, str]]:
    """→ [(url, kind)] не больше limit, в порядке приоритета. Главная всегда первая."""
    rp, sitemap_urls = await robots(http, home)
    agent = settings.user_agent
    found: dict[str, datetime | None] = {}
    queue, seen = list(sitemap_urls), set()
    while queue and len(seen) < MAX_SITEMAPS:
        sm = queue.pop(0)
        if sm in seen or not same_site(sm, home):
            continue
        seen.add(sm)
        try:
            resp = await http.get(sm)
        except FetchError:
            continue
        if resp.status_code != 200:
            continue
        pages, nested = parse_sitemap(resp.content)
        queue.extend(nested)
        for loc, dt in pages[:MAX_CANDIDATES]:
            found.setdefault(loc, dt)
    for link in links(home_html, home):
        found.setdefault(link, None)

    candidates: dict[str, tuple[str, datetime | None]] = {normalize(home): ("home", None)}
    for url, dt in found.items():
        if not same_site(url, home) or SKIP_EXT.search(urlsplit(url).path) or SKIP_PATH.search(url):
            continue
        norm = normalize(url)
        if norm in candidates or not rp.can_fetch(agent, norm):
            continue
        candidates[norm] = (classify(norm, home), dt)
    ordered = sorted(candidates.items(), key=lambda kv: (
        PRIORITY[kv[1][0]], -(kv[1][1].timestamp() if kv[1][1] else 0), len(kv[0])))
    return [(url, kind) for url, (kind, _) in ordered[:limit]]
