"""Темы в цифрах. Всё считает код, модель только объясняет.

Доля темы у клиента = посты клиента на тему / все размеченные посты клиента за период; доля рынка — то же по
постам конкурентов и рынка. gap = доля рынка − доля клиента (п.п.): положительный — рынок пишет, клиент нет.
Тренд — изменение доли рынка относительно предыдущего периода той же длины."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from statistics import mean, median

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis import dedupe
from app.analysis.taxonomy import OTHER_TOPIC
from app.core.db import utcnow
from app.models import Competitor, GlobalPost, PostAnalysis, Source, SourceRole

GAP_MIN_PP = 5.0       # разница долей, с которой тема считается пробелом
GAP_MIN_MARKET = 3     # и минимум публикаций рынка на тему — иначе это шум
MARKET_ROLES = (SourceRole.competitor, SourceRole.market)


async def labelled(session: AsyncSession, org_id: int, since: datetime, until: datetime):
    stmt = (select(GlobalPost, PostAnalysis, Source)
            .join(PostAnalysis, and_(PostAnalysis.post_id == GlobalPost.id, PostAnalysis.organization_id == org_id,
                                     PostAnalysis.error.is_(None)))
            .join(Source, and_(Source.global_source_id == GlobalPost.global_source_id,
                               Source.organization_id == org_id, Source.enabled.is_(True)))
            .where(GlobalPost.published_at >= since, GlobalPost.published_at < until, dedupe.not_hidden(org_id)))
    return (await session.execute(stmt)).all()


def _pct(part: int, whole: int) -> float:
    return round(100 * part / whole, 1) if whole else 0.0


def aggregate(rows, days: int, prev_rows=(), competitors: dict[int, str] | None = None) -> dict:
    competitors = competitors or {}
    own_total = sum(1 for r in rows if r.Source.role == SourceRole.own)
    market_total = sum(1 for r in rows if r.Source.role in MARKET_ROLES)
    prev_market = [r for r in prev_rows if r.Source.role in MARKET_ROLES]
    prev_counts = Counter(r.PostAnalysis.topic for r in prev_market)

    by_topic: dict[str, list] = defaultdict(list)
    for r in rows:
        by_topic[r.PostAnalysis.topic or OTHER_TOPIC].append(r)
    topics = []
    for topic, rs in by_topic.items():
        own = [r for r in rs if r.Source.role == SourceRole.own]
        comp = [r for r in rs if r.Source.role == SourceRole.competitor]
        market = [r for r in rs if r.Source.role in MARKET_ROLES]
        eng = [r.GlobalPost.engagement for r in market if r.GlobalPost.engagement is not None]
        ers = [r.GlobalPost.er for r in market if r.GlobalPost.er is not None]
        share_market, share_own = _pct(len(market), market_total), _pct(len(own), own_total)
        share_prev = _pct(prev_counts[topic], len(prev_market))
        comp_ids = {r.Source.competitor_id for r in comp if r.Source.competitor_id}
        gap = round(share_market - share_own, 1)
        topics.append({
            "topic": topic, "own": len(own), "competitor": len(comp), "market": len(market) - len(comp),
            "market_total": len(market), "share_own": share_own, "share_market": share_market,
            "gap": gap, "is_gap": gap >= GAP_MIN_PP and len(market) >= GAP_MIN_MARKET and topic != OTHER_TOPIC,
            "trend_pp": round(share_market - share_prev, 1) if prev_market else None,
            "prev_market": prev_counts[topic],
            "saturation_per_week": round(len(market) / (days / 7), 1),
            "competitors": sorted(competitors.get(c, "?") for c in comp_ids),
            "median_er": round(median(ers), 2) if ers else None,
            "median_engagement": median(eng) if eng else None,
            "mean_engagement": round(mean(eng), 1) if eng else None,
        })
    topics.sort(key=lambda t: (t["market_total"] + t["own"]), reverse=True)
    return {"days": days, "own_total": own_total, "market_total": market_total,
            "prev_market_total": len(prev_market), "topics": topics}


async def competitor_names(session: AsyncSession, org_id: int) -> dict[int, str]:
    rows = await session.execute(select(Competitor.id, Competitor.name).where(Competitor.organization_id == org_id))
    return dict(rows.all())


async def overview(session: AsyncSession, org_id: int, days: int) -> dict:
    now = utcnow()
    rows = await labelled(session, org_id, now - timedelta(days=days), now)
    prev = await labelled(session, org_id, now - timedelta(days=2 * days), now - timedelta(days=days))
    return aggregate(rows, days, prev, await competitor_names(session, org_id))


def weekly(rows, days: int) -> list[dict]:
    now = utcnow()
    weeks = max(1, min(26, days // 7))
    start0 = now - timedelta(weeks=weeks)
    out = []
    for i in range(weeks):
        a, b = start0 + timedelta(weeks=i), start0 + timedelta(weeks=i + 1)
        wr = [r for r in rows if a <= r.GlobalPost.published_at < b]
        out.append({"week": a.date().isoformat(),
                    "own": sum(1 for r in wr if r.Source.role == SourceRole.own),
                    "competitor": sum(1 for r in wr if r.Source.role == SourceRole.competitor),
                    "market": sum(1 for r in wr if r.Source.role == SourceRole.market)})
    return out


def shares(values: list[str], top: int = 5) -> list[dict]:
    total = len(values)
    return [{"value": v, "count": n, "share": _pct(n, total)} for v, n in Counter(values).most_common(top)]
