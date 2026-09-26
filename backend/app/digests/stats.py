"""Цифры дайджеста за период — всё считает код, сравнение с предыдущим периодом той же длины.

Рынок (объём, вовлечённость), темы (растущие, новые, пробелы), конкуренты (публикации, лучший пост, изменения сайта),
лучшие и «выстрелившие» публикации, новые форматы у конкурентов, собственный контент клиента, темы «Стратегии»."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from statistics import median

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis import dedupe
from app.analysis.taxonomy import OTHER_TOPIC
from app.models import (
    Competitor,
    ContentAudit,
    ContentOpportunity,
    GlobalPost,
    GlobalSource,
    PostAnalysis,
    Source,
    SourceRole,
    Website,
    WebsiteChange,
    WebsitePage,
)
from app.topics import stats as topic_stats

MARKET = (SourceRole.competitor, SourceRole.market)
OUTLIER = 2.0          # во сколько раз выше медианы источника — «выстрелил»
TOP_N = 5
FORMAT_HISTORY_DAYS = 90


async def rows(session: AsyncSession, org_id: int, since: datetime, until: datetime):
    stmt = (select(GlobalPost, PostAnalysis, Source, GlobalSource, Competitor.name)
            .join(GlobalSource, GlobalSource.id == GlobalPost.global_source_id)
            .join(Source, and_(Source.global_source_id == GlobalSource.id, Source.organization_id == org_id,
                               Source.enabled.is_(True)))
            .outerjoin(PostAnalysis, and_(PostAnalysis.post_id == GlobalPost.id, PostAnalysis.organization_id == org_id,
                                          PostAnalysis.error.is_(None)))
            .outerjoin(Competitor, Competitor.id == Source.competitor_id)
            .where(GlobalPost.published_at >= since, GlobalPost.published_at < until, dedupe.not_hidden(org_id)))
    return (await session.execute(stmt)).all()


def _med(values) -> float | None:
    values = [v for v in values if v is not None]
    return round(median(values), 2) if values else None


def _delta(now: int, prev: int) -> float | None:
    return round(100 * (now - prev) / prev, 1) if prev else None


def post_ref(r) -> dict:
    p, a, s, gs, comp = r
    return {"post_id": p.id, "url": p.url, "date": p.published_at.date().isoformat() if p.published_at else None,
            "source": comp or s.name or gs.title or gs.key, "role": s.role.value, "format": p.media_type,
            "topic": a.topic if a else None, "text": "\n".join(filter(None, [p.title, p.text]))[:400],
            "er": p.er, "overperformance": p.overperformance}


async def build(session: AsyncSession, org_id: int, until: datetime, days: int) -> dict:
    since, prev_since = until - timedelta(days=days), until - timedelta(days=2 * days)
    cur, prev = await rows(session, org_id, since, until), await rows(session, org_id, prev_since, since)
    market, prev_market = [r for r in cur if r[2].role in MARKET], [r for r in prev if r[2].role in MARKET]
    own, prev_own = [r for r in cur if r[2].role == SourceRole.own], [r for r in prev if r[2].role == SourceRole.own]

    # темы — только по размеченным постам (как во вкладке «Темы»)
    labelled = await topic_stats.labelled(session, org_id, since, until)
    prev_labelled = await topic_stats.labelled(session, org_id, prev_since, since)
    names = await topic_stats.competitor_names(session, org_id)
    agg = topic_stats.aggregate(labelled, days, prev_labelled, names)
    topics = [t for t in agg["topics"] if t["topic"] != OTHER_TOPIC]
    rising = sorted((t for t in topics if (t["trend_pp"] or 0) > 0 and t["market_total"] >= 2),
                    key=lambda t: -t["trend_pp"])[:TOP_N]
    new = [t for t in topics if t["prev_market"] == 0 and t["market_total"] >= 2][:TOP_N]
    gaps = sorted((t for t in topics if t["is_gap"]), key=lambda t: -t["gap"])[:TOP_N]

    # конкуренты: публикации, отклик, лучший пост, новые форматы
    history = await rows(session, org_id, until - timedelta(days=FORMAT_HISTORY_DAYS + days), since)
    old_formats: dict[int, set[str]] = defaultdict(set)
    for r in history:
        if r[2].competitor_id:
            old_formats[r[2].competitor_id].add(r[0].media_type)
    by_comp: dict[int, list] = defaultdict(list)
    prev_by_comp = Counter(r[2].competitor_id for r in prev_market if r[2].competitor_id)
    for r in market:
        if r[2].competitor_id:
            by_comp[r[2].competitor_id].append(r)
    changes = (await session.execute(
        select(WebsiteChange, Website, WebsitePage).join(Website, Website.id == WebsiteChange.website_id)
        .join(WebsitePage, WebsitePage.id == WebsiteChange.page_id)
        .where(Website.organization_id == org_id, WebsiteChange.detected_at >= since, WebsiteChange.detected_at < until,
               WebsiteChange.importance.in_(("high", "medium")))
        .order_by(WebsiteChange.detected_at.desc()))).all()
    site_by_comp: dict[int | None, list] = defaultdict(list)
    for c, w, p in changes:
        site_by_comp[w.competitor_id].append({"site": w.name or w.url, "page": p.title or p.url, "url": p.url,
                                              "kind": c.kind, "summary": c.summary, "category": c.category,
                                              "importance": c.importance})
    competitors = []
    for cid, name in sorted(names.items(), key=lambda kv: kv[1]):
        rs = by_comp.get(cid, [])
        best = max(rs, key=lambda r: r[0].overperformance or 0, default=None)
        fresh = sorted({r[0].media_type for r in rs} - old_formats[cid]) if old_formats[cid] else []
        if not rs and not prev_by_comp[cid] and not site_by_comp.get(cid):
            continue
        competitors.append({"id": cid, "name": name, "posts": len(rs), "prev_posts": prev_by_comp[cid],
                            "median_er": _med(r[0].er for r in rs), "new_formats": fresh,
                            "best_post": post_ref(best) if best else None,
                            "site_changes": site_by_comp.get(cid, [])[:5]})
    competitors.sort(key=lambda c: (len(c["site_changes"]) + c["posts"]), reverse=True)

    top = sorted((r for r in market if r[0].overperformance), key=lambda r: r[0].overperformance, reverse=True)
    outliers = [post_ref(r) for r in top if r[0].overperformance >= OUTLIER][:TOP_N]
    own_best = max(own, key=lambda r: r[0].overperformance or 0, default=None)
    audit = (await session.execute(select(ContentAudit).where(
        ContentAudit.organization_id == org_id, ContentAudit.stage == "done", ContentAudit.finished_at >= since)
        .order_by(ContentAudit.id.desc()).limit(1))).scalar_one_or_none()
    opps = (await session.execute(select(ContentOpportunity).where(
        ContentOpportunity.organization_id == org_id, ContentOpportunity.status == "new")
        .order_by(ContentOpportunity.rank).limit(3))).scalars().all()

    return {
        "period": {"from": since.isoformat(), "to": until.isoformat(), "days": days},
        "enough": len(market) + len(own) > 0 or bool(changes),
        "market": {"posts": len(market), "prev_posts": len(prev_market), "delta_pct": _delta(len(market),
                                                                                           len(prev_market)),
                   "sources": len({r[2].id for r in market}), "median_er": _med(r[0].er for r in market),
                   "prev_median_er": _med(r[0].er for r in prev_market),
                   "formats": topic_stats.shares([r[0].media_type for r in market], top=4)},
        "topics": {"rising": [{k: t[k] for k in ("topic", "share_market", "trend_pp", "market_total")} for t in rising],
                   "new": [{k: t[k] for k in ("topic", "market_total", "competitors")} for t in new],
                   "gaps": [{k: t[k] for k in ("topic", "share_market", "share_own", "gap")} for t in gaps]},
        "competitors": competitors,
        "other_site_changes": site_by_comp.get(None, [])[:5],
        "top_posts": [post_ref(r) for r in top[:TOP_N]],
        "outliers": outliers,
        "own": {"posts": len(own), "prev_posts": len(prev_own), "delta_pct": _delta(len(own), len(prev_own)),
                "median_er": _med(r[0].er for r in own), "prev_median_er": _med(r[0].er for r in prev_own),
                "best_post": post_ref(own_best) if own_best else None,
                "audit": {"id": audit.id, "score": audit.score} if audit else None},
        "opportunities": [{"id": o.id, "title": o.title, "why": o.why, "formats": o.formats} for o in opps],
    }
