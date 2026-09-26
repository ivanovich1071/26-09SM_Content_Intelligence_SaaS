"""RAG-контекст для Writer: всё собирает код из данных организации.

Лучшие публикации рынка по теме — поиск по смыслу (pgvector) среди видимых организации постов конкурентов и рынка,
из ближайших берутся лучшие по overperformance. Без эмбеддингов — посты темы таксономии или поиск по словам.
Плюс приёмы темы (хуки, CTA, типы), прошлые посты клиента, тема из «Стратегии» и слабые места аудита."""
import re
from datetime import timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embeddings import EmbeddingError, EmbeddingProvider
from app.analysis import dedupe
from app.analysis.embed import embed_query
from app.audits.criteria import BY_KEY
from app.billing.quotas import QuotaExceeded
from app.core.db import utcnow
from app.models import (
    AuditItem,
    Competitor,
    ContentAudit,
    ContentOpportunity,
    ContentProject,
    GlobalPost,
    GlobalSource,
    PostAnalysis,
    PostEmbedding,
    Source,
    SourceRole,
)
from app.topics.stats import MARKET_ROLES, shares

DAYS = 365
MARKET_POSTS, OWN_POSTS, NEAREST = 5, 3, 15


def _base(org_id: int, roles):
    return (select(GlobalPost, PostAnalysis, Source, GlobalSource, Competitor.name)
            .join(GlobalSource, GlobalSource.id == GlobalPost.global_source_id)
            .join(Source, and_(Source.global_source_id == GlobalSource.id, Source.organization_id == org_id,
                               Source.enabled.is_(True), Source.role.in_(roles)))
            .outerjoin(PostAnalysis, and_(PostAnalysis.post_id == GlobalPost.id,
                                          PostAnalysis.organization_id == org_id))
            .outerjoin(Competitor, Competitor.id == Source.competitor_id)
            .where(dedupe.not_hidden(org_id), GlobalPost.published_at >= utcnow() - timedelta(days=DAYS)))


def post_ref(row) -> dict:
    p, _, s, gs, comp = row[:5]
    return {"post_id": p.id, "url": p.url, "date": p.published_at.date().isoformat() if p.published_at else None,
            "source": comp or s.name or gs.title or gs.key, "competitor": s.role == SourceRole.competitor,
            "text": "\n".join(filter(None, [p.title, p.text]))[:700], "er": p.er, "overperformance": p.overperformance}


def _best(rows, n: int) -> list:
    return sorted(rows, key=lambda r: (r[0].overperformance or 0, r[0].er or 0), reverse=True)[:n]


def _words(query: str) -> list[str]:
    return sorted({w for w in re.findall(r"\w{5,}", query.lower())}, key=len, reverse=True)[:4]


async def retrieve(session: AsyncSession, org_id: int, query: str, topic: str | None,
                   embedder: EmbeddingProvider | None) -> tuple[list, list, str | None]:
    """→ (посты рынка, свои посты, способ поиска)."""
    if embedder is not None:
        try:
            vec = await embed_query(session, embedder, query, org_id=org_id)
        except (EmbeddingError, QuotaExceeded):  # до записи в сессию — откатывать нечего
            pass
        else:
            dist = PostEmbedding.embedding.cosine_distance(vec)
            near = lambda roles, n: (_base(org_id, roles).join(PostEmbedding, PostEmbedding.post_id == GlobalPost.id)  # noqa: E731
                                     .order_by(dist).limit(n))
            market = (await session.execute(near(MARKET_ROLES, NEAREST))).all()
            own = (await session.execute(near((SourceRole.own,), OWN_POSTS))).all()
            if market or own:
                return _best(market, MARKET_POSTS), list(own), "semantic"
    if topic:
        by_topic = lambda roles: _base(org_id, roles).where(PostAnalysis.topic == topic)  # noqa: E731
        market = (await session.execute(by_topic(MARKET_ROLES))).all()
        own = (await session.execute(by_topic((SourceRole.own,)).order_by(GlobalPost.published_at.desc())
                                     .limit(OWN_POSTS))).all()
        if market:
            return _best(market, MARKET_POSTS), list(own), "topic"
    words = _words(query)
    if not words:
        return [], [], None
    match = or_(*[GlobalPost.text.ilike(f"%{w}%") for w in words], *[GlobalPost.title.ilike(f"%{w}%") for w in words])
    market = (await session.execute(_base(org_id, MARKET_ROLES).where(match).limit(200))).all()
    own = (await session.execute(_base(org_id, (SourceRole.own,)).where(match)
                                 .order_by(GlobalPost.published_at.desc()).limit(OWN_POSTS))).all()
    return _best(market, MARKET_POSTS), list(own), "text" if market or own else None


async def patterns(session: AsyncSession, org_id: int, topic: str | None) -> dict:
    if not topic:
        return {}
    rows = (await session.execute(_base(org_id, MARKET_ROLES).where(PostAnalysis.topic == topic,
                                                                    PostAnalysis.error.is_(None)))).all()
    labels = [r[1] for r in rows]
    top = [r[1] for r in _best(rows, max(3, len(rows) // 4))]  # верхняя четверть по отклику
    return {"публикаций_рынка": len(rows),
            "хуки_сильных_постов": shares([a.hook_type for a in top if a.hook_type != "нет"], top=3),
            "призывы": shares([a.cta_type for a in labels if a.cta_type != "нет"], top=3),
            "типы_контента": shares([a.content_type for a in labels], top=3),
            "доказательства": shares([a.proof_type for a in labels if a.proof_type != "нет"], top=3)}


async def audit_notes(session: AsyncSession, org_id: int) -> dict | None:
    audit = (await session.execute(select(ContentAudit).where(
        ContentAudit.organization_id == org_id, ContentAudit.stage == "done").order_by(ContentAudit.id.desc())
        .limit(1))).scalar_one_or_none()
    if audit is None:
        return None
    items = (await session.execute(select(AuditItem).where(AuditItem.audit_id == audit.id))).scalars().all()
    weak = sorted((i for i in items if i.score is not None and i.score < 6), key=lambda i: i.score)
    return {"audit_id": audit.id,
            "слабые_места": [{"критерий": BY_KEY[i.criterion].name, "балл": i.score,
                              "советы": (i.recommendations or [])[:2]} for i in weak]}


async def build(session: AsyncSession, project: ContentProject, embedder: EmbeddingProvider | None) -> dict:
    org_id = project.organization_id
    opp = await session.get(ContentOpportunity, project.opportunity_id) if project.opportunity_id else None
    topic = opp.topic if opp else None
    market, own, mode = await retrieve(session, org_id, f"{project.title}\n{project.brief or ''}", topic, embedder)
    if topic is None:  # тема не из «Стратегии» — берём самую частую тему найденных постов
        topics = [r[1].topic for r in market if r[1] is not None and r[1].topic]
        topic = max(set(topics), key=topics.count) if topics else None
    return {
        "search": mode, "topic": topic,
        "opportunity": {"почему": opp.why, "угол": opp.angle, "цифры": {k: opp.market.get(k) for k in (
            "share_market", "share_own", "gap", "trend_pp", "median_er", "market_median_er")}} if opp else None,
        "market_posts": [post_ref(r) for r in market],
        "own_posts": [post_ref(r) for r in own],
        "patterns": await patterns(session, org_id, topic),
        "audit": await audit_notes(session, org_id),
    }
