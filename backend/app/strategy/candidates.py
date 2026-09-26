"""Кандидаты в темы — всё считает код: темы таксономии с долями рынка и клиента, gap, трендом, ER и примерами.

Оценка кандидата (score): пробел (п.п.) + половина роста доли рынка + бонус за вовлечённость выше медианы рынка
− штраф за перенасыщенность. Модель выбирает и формулирует темы только из этих кандидатов."""
from collections import defaultdict
from datetime import timedelta
from statistics import median

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.taxonomy import OTHER_TOPIC
from app.core.config import settings
from app.core.db import utcnow
from app.models import SourceRole, TopicCluster
from app.topics import stats

MIN_TOPIC_MARKET = 3   # меньше публикаций рынка по теме — шум
EXAMPLES = 3
MAX_CANDIDATES = 14


def score(t: dict, market_er: float | None) -> float:
    s = max(t["gap"], 0.0) + 0.5 * max(t["trend_pp"] or 0.0, 0.0)
    if market_er and t["median_er"] is not None:
        s += max(-5.0, min(10.0, 10 * (t["median_er"] / market_er - 1)))
    if t["saturation_per_week"] > 10:  # рынок и так завален темой — отстроиться сложнее
        s -= 3
    return round(s, 1)


def _example(r, names: dict[int, str]) -> dict:
    p, src = r.GlobalPost, r.Source
    return {"post_id": p.id, "url": p.url, "date": p.published_at.date().isoformat() if p.published_at else None,
            "source": names.get(src.competitor_id) if src.competitor_id else (src.name or src.global_source.title
                                                                               or src.global_source.key),
            "competitor": src.role == SourceRole.competitor, "text": "\n".join(filter(None, [p.title, p.text]))[:300],
            "er": p.er, "overperformance": p.overperformance, "format": p.media_type}


async def build(session: AsyncSession, org_id: int, days: int) -> dict:
    now = utcnow()
    rows = await stats.labelled(session, org_id, now - timedelta(days=days), now)
    prev = await stats.labelled(session, org_id, now - timedelta(days=2 * days), now - timedelta(days=days))
    names = await stats.competitor_names(session, org_id)
    agg = stats.aggregate(rows, days, prev, names)
    market_rows = [r for r in rows if r.Source.role in stats.MARKET_ROLES]
    ers = [r.GlobalPost.er for r in market_rows if r.GlobalPost.er is not None]
    market_er = round(median(ers), 2) if ers else None

    by_topic: dict[str, list] = defaultdict(list)
    for r in market_rows:
        by_topic[r.PostAnalysis.topic or OTHER_TOPIC].append(r)
    clusters: dict[str, list[str]] = defaultdict(list)
    for c in (await session.execute(select(TopicCluster).where(TopicCluster.organization_id == org_id)
                                    .order_by(TopicCluster.size.desc()))).scalars():
        clusters[c.topic].append(c.label)

    out = []
    for t in agg["topics"]:
        if t["topic"] == OTHER_TOPIC or t["market_total"] < MIN_TOPIC_MARKET:
            continue
        top, texts = [], set()
        for r in sorted(by_topic[t["topic"]], key=lambda r: (r.GlobalPost.overperformance or 0, r.GlobalPost.er or 0),
                        reverse=True):
            key = " ".join((r.GlobalPost.text or r.GlobalPost.title or "").lower().split())[:200]
            if key not in texts:  # репосты и одинаковые анонсы — один пример
                texts.add(key)
                top.append(r)
            if len(top) >= EXAMPLES:
                break
        market = {k: t[k] for k in ("share_market", "share_own", "gap", "trend_pp", "median_er", "saturation_per_week",
                                    "market_total", "own", "competitors")}
        market["market_median_er"] = market_er
        out.append({"topic": t["topic"], "score": score(t, market_er), "market": market,
                    "subtopics": clusters[t["topic"]][:5], "examples": [_example(r, names) for r in top],
                    "formats": stats.shares([r.GlobalPost.media_type for r in by_topic[t["topic"]]], top=3)})
    out.sort(key=lambda c: c["score"], reverse=True)
    for i, c in enumerate(out[:MAX_CANDIDATES], 1):
        c["id"] = f"c{i}"
    return {"days": days, "market_posts": agg["market_total"], "own_posts": agg["own_total"],
            "market_median_er": market_er, "enough": agg["market_total"] >= settings.strategy_min_market_posts,
            "candidates": out[:MAX_CANDIDATES]}
