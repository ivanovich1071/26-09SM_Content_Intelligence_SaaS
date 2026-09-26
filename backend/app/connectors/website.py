"""Сайт как источник публикаций: находим RSS/Atom блога и собираем через него.

Снимки страниц, diff и изменения офферов — вкладка «Сайты» (EPIC 7); здесь только профиль
(заголовок, описание, соцсети — пригодятся для автопоиска источников конкурента в EPIC 4) и статьи.
extract_text / extract_social / diff_lines — перенос из VM_SM site_watch.py."""
import difflib
import re
from datetime import datetime
from urllib.parse import urljoin, urlsplit

from selectolax.parser import HTMLParser

from app.connectors.base import CollectResult, SourceConnector, SourceProfile, normalize_web_url
from app.connectors.http import Fetcher, FetchError
from app.connectors.rss import FeedError, RSSConnector, parse_feed

SOCIAL_RE = re.compile(
    r"https?://(?:www\.)?(?:t\.me|telegram\.me|instagram\.com|youtube\.com|youtu\.be|vk\.com|tiktok\.com|"
    r"linkedin\.com|facebook\.com|dzen\.ru|threads\.net|x\.com|twitter\.com)/[A-Za-z0-9_.@/-]+",
    re.I,
)
# Ссылки, которые не являются аккаунтами: встроенные видео, шаринг, пиксели
SOCIAL_SKIP = re.compile(r"/(embed|share|sharer|intent|watch)\b|youtube\.com/(iframe_api|s/)|facebook\.com/tr\b", re.I)
FEED_TYPES = ("application/rss+xml", "application/atom+xml", "application/feed+xml")
FEED_GUESSES = ("/feed", "/rss", "/rss.xml", "/feed.xml", "/atom.xml", "/blog/feed", "/blog/rss")
MIN_LINE_LEN = 3


def extract_text(html: str) -> str:
    tree = HTMLParser(html)
    for tag in tree.css("script, style, noscript, svg, template"):
        tag.decompose()
    body = tree.body or tree.root
    raw = body.text(separator="\n") if body else ""
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in raw.splitlines()]
    return "\n".join(ln for ln in lines if len(ln) >= MIN_LINE_LEN)


def extract_social(html: str) -> list[str]:
    found = set()
    for m in SOCIAL_RE.findall(html):
        url = m.rstrip("/.").split("?")[0]
        if SOCIAL_SKIP.search(url):
            continue
        found.add(url.replace("http://", "https://").replace("://www.", "://").lower())
    return sorted(found)


def diff_lines(old: str, new: str) -> tuple[list[str], list[str]]:
    added, removed = [], []
    for ln in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0):
        if ln.startswith("+") and not ln.startswith("+++"):
            added.append(ln[1:])
        elif ln.startswith("-") and not ln.startswith("---"):
            removed.append(ln[1:])
    return added, removed


def page_meta(html: str, base_url: str) -> dict:
    tree = HTMLParser(html)
    title = tree.css_first("meta[property='og:title']") or tree.css_first("title")
    desc = tree.css_first("meta[name='description']") or tree.css_first("meta[property='og:description']")
    feeds = []
    for link in tree.css("link[rel='alternate'][href]"):
        if (link.attributes.get("type") or "").lower() in FEED_TYPES:
            feeds.append(urljoin(base_url, link.attributes["href"]))
    if title is not None:
        title_text = title.attributes.get("content") if title.tag == "meta" else title.text(strip=True)
    else:
        title_text = None
    return {
        "title": title_text,
        "description": desc.attributes.get("content") if desc else None,
        "feeds": list(dict.fromkeys(feeds)),
    }


class WebsiteConnector(SourceConnector):
    kind = "website"

    def __init__(self):
        self.rss = RSSConnector()

    def normalize(self, raw: str) -> tuple[str, str]:
        url = normalize_web_url(raw)
        parts = urlsplit(url)
        key = f"{parts.netloc.removeprefix('www.')}{parts.path.rstrip('/')}"
        return key, url

    async def _find_feed(self, http: Fetcher, url: str, declared: list[str]) -> str | None:
        root = f"{urlsplit(url).scheme}://{urlsplit(url).netloc}"
        for candidate in [*declared, *(root + g for g in FEED_GUESSES)]:
            try:
                resp = await http.get(candidate)
                if resp.status_code == 200:
                    parse_feed(resp.content)
                    return candidate
            except (FetchError, FeedError):
                continue
        return None

    async def get_profile(self, http: Fetcher, key: str, url: str, meta: dict) -> SourceProfile:
        resp = await http.get(url)
        if resp.status_code != 200:
            return SourceProfile(available=False, reason=f"Сайт ответил HTTP {resp.status_code}")
        info = page_meta(resp.text, str(resp.url) or url)
        feed = await self._find_feed(http, url, info["feeds"])
        return SourceProfile(available=True, title=info["title"], description=info["description"],
                             meta={"feed_url": feed, "social_links": extract_social(resp.text)})

    async def collect(self, http: Fetcher, key: str, url: str, meta: dict, *, since: datetime,
                      known_ids: set[str]) -> CollectResult:
        if not meta.get("feed_url"):
            return CollectResult(items=[], pages=0)
        return await self.rss.collect(http, meta["feed_url"], meta["feed_url"], {}, since=since, known_ids=known_ids)
