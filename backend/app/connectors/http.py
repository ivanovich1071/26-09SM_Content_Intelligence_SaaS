"""HTTP для коннекторов: пауза между запросами к домену (общая для всех воркеров через Redis)
и запрет запросов во внутреннюю сеть — адреса источников вводят пользователи."""
import asyncio
import ipaddress
import socket
import time
from urllib.parse import urljoin, urlsplit

import httpx
from redis.asyncio import Redis

from app.core.config import settings

MAX_REDIRECTS = 5


class FetchError(Exception):
    """Сетевая ошибка или запрещённый адрес. Сообщение показывается пользователю."""


class DomainRateLimiter:
    """Не чаще одного запроса к домену в `interval` секунд.

    С Redis — `SET NX PX` (ведро на один токен, общее для всех процессов); без Redis — в памяти процесса."""

    def __init__(self, redis: Redis | None = None, interval: float | None = None):
        self.redis = redis
        self.interval = settings.domain_min_interval_sec if interval is None else interval
        self._next: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def wait(self, domain: str) -> None:
        if self.interval <= 0:
            return
        if self.redis is not None:
            try:
                await self._wait_redis(domain)
                return
            except Exception:  # noqa: BLE001 — Redis упал посреди сбора: лимитируем локально
                self.redis = None
        async with self._lock:
            now = time.monotonic()
            start = max(now, self._next.get(domain, 0.0))
            self._next[domain] = start + self.interval
        if start > now:
            await asyncio.sleep(start - now)

    async def _wait_redis(self, domain: str) -> None:
        key, ms = f"rl:domain:{domain}", int(self.interval * 1000)
        while not await self.redis.set(key, 1, nx=True, px=ms):
            ttl = await self.redis.pttl(key)
            await asyncio.sleep(max(ttl, 50) / 1000)


def _is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return addr.is_global and not addr.is_multicast


async def ensure_public_host(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchError(f"Неподдерживаемый адрес: {url}")
    host = parts.hostname
    try:
        ips = [ipaddress.ip_address(host).compressed]
    except ValueError:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except socket.gaierror as e:
            raise FetchError(f"Домен {host} не найден") from e
        ips = [info[4][0] for info in infos]
    if not ips or not all(_is_public(ip) for ip in ips):
        raise FetchError(f"Адрес {host} недоступен для сбора (внутренняя сеть)")


class Fetcher:
    """Один на задачу. `transport` и `check_hosts=False` — для тестов с httpx.MockTransport."""

    def __init__(self, limiter: DomainRateLimiter | None = None, transport: httpx.AsyncBaseTransport | None = None,
                 check_hosts: bool = True):
        self.limiter = limiter or DomainRateLimiter()
        self.check_hosts = check_hosts
        self.client = httpx.AsyncClient(headers={"User-Agent": settings.user_agent}, timeout=30,
                                        follow_redirects=False, transport=transport)

    async def __aenter__(self) -> "Fetcher":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.client.aclose()

    async def get(self, url: str, *, follow_redirects: bool = True) -> httpx.Response:
        """Редиректы проходим вручную, чтобы проверить каждый адрес. Тело режется по fetch_max_bytes."""
        for _ in range(MAX_REDIRECTS + 1):
            if self.check_hosts:
                await ensure_public_host(url)
            await self.limiter.wait(urlsplit(url).hostname or "")
            try:
                async with self.client.stream("GET", url) as resp:
                    chunks, size = [], 0
                    async for chunk in resp.aiter_bytes():
                        chunks.append(chunk)
                        size += len(chunk)
                        if size > settings.fetch_max_bytes:
                            break
                    resp._content = b"".join(chunks)
            except httpx.HTTPError as e:
                raise FetchError(f"Ошибка сети: {type(e).__name__}") from e
            if follow_redirects and resp.is_redirect and "location" in resp.headers:
                url = urljoin(url, resp.headers["location"])
                continue
            return resp
        raise FetchError("Слишком много перенаправлений")
