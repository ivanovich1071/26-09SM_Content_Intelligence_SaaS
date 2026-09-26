"""Карточка темы и AI-объяснение Content Gap (Topic Analyst)."""
import json
from datetime import timedelta

import numpy as np
from pgvector.sqlalchemy import Vector
from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import prompts
from app.ai.router import AIRouter, model_for
from app.analysis import dedupe, taxonomy
from app.core.config import settings
from app.core.db import utcnow
from app.models import (
    GlobalPost,
    PostAnalysis,
    PostEmbedding,
    Source,
    SourceRole,
    TopicCluster,
    TopicClusterPost,
    TopicInsight,
)
from app.topics import stats

TOP_POSTS = 6
RELATED = 3


async def related_topics(session: AsyncSession, org_id: int, topic: str, since) -> list[dict]:
    """Близость тем — косинус средних эмбеддингов их постов."""
    stmt = (select(PostAnalysis.topic, func.avg(PostEmbedding.embedding, type_=Vector(settings.embedding_dim)))
            .join(PostEmbedding, PostEmbedding.post_id == PostAnalysis.post_id)
            .join(GlobalPost, GlobalPost.id == PostAnalysis.post_id)
            .where(PostAnalysis.organization_id == org_id, PostAnalysis.error.is_(None),
                   GlobalPost.published_at >= since, PostAnalysis.topic.is_not(None))
            .group_by(PostAnalysis.topic))
    means = {t: np.array(v, dtype=float) for t, v in (await session.execute(stmt)).all()}
    if topic not in means:
        return []
    base = means.pop(topic)
    scored = []
    for t, v in means.items():
        if t == taxonomy.OTHER_TOPIC:
            continue
        denom = np.linalg.norm(base) * np.linalg.norm(v)
        if denom:
            scored.append({"topic": t, "similarity": round(float(base @ v / denom), 3)})
    return sorted(scored, key=lambda x: x["similarity"], reverse=True)[:RELATED]


async def subtopics(session: AsyncSession, org_id: int, topic: str) -> list[dict]:
    clusters = (await session.execute(select(TopicCluster).where(
        TopicCluster.organization_id == org_id, TopicCluster.topic == topic).order_by(TopicCluster.size.desc())
    )).scalars().all()
    out = []
    for c in clusters:
        roles = dict((await session.execute(
            select(Source.role, func.count()).select_from(TopicClusterPost)
            .join(GlobalPost, GlobalPost.id == TopicClusterPost.post_id)
            .join(Source, and_(Source.global_source_id == GlobalPost.global_source_id,
                               Source.organization_id == org_id))
            .where(TopicClusterPost.cluster_id == c.id).group_by(Source.role))).all())
        out.append({"id": c.id, "label": c.label, "description": c.description, "keywords": c.keywords,
                    "size": c.size, "own": roles.get(SourceRole.own, 0),
                    "competitor": roles.get(SourceRole.competitor, 0), "market": roles.get(SourceRole.market, 0),
                    "created_at": c.created_at})
    return out


async def detail(session: AsyncSession, org_id: int, topic: str, days: int) -> dict | None:
    now = utcnow()
    since = now - timedelta(days=days)
    rows = await stats.labelled(session, org_id, since, now)
    prev = await stats.labelled(session, org_id, now - timedelta(days=2 * days), since)
    names = await stats.competitor_names(session, org_id)
    agg = stats.aggregate(rows, days, prev, names)
    row = next((t for t in agg["topics"] if t["topic"] == topic), None)
    if row is None:
        return None
    rs = [r for r in rows if r.PostAnalysis.topic == topic]
    market = [r for r in rs if r.Source.role in stats.MARKET_ROLES]
    top = sorted((r for r in market if r.GlobalPost.overperformance), key=lambda r: r.GlobalPost.overperformance,
                 reverse=True)[:TOP_POSTS]
    by_comp: dict[str, int] = {}
    for r in rs:
        if r.Source.role == SourceRole.competitor and r.Source.competitor_id:
            name = names.get(r.Source.competitor_id, "?")
            by_comp[name] = by_comp.get(name, 0) + 1
    return {
        **row, "totals": {"own": agg["own_total"], "market": agg["market_total"]},
        "weekly": stats.weekly(rs, days),
        "formats_market": stats.shares([r.GlobalPost.media_type for r in market]),
        "formats_own": stats.shares([r.GlobalPost.media_type for r in rs if r.Source.role == SourceRole.own]),
        "content_types": stats.shares([r.PostAnalysis.content_type for r in market if r.PostAnalysis.content_type]),
        "hooks": stats.shares([r.PostAnalysis.hook_type for r in market
                               if r.PostAnalysis.hook_type not in (None, "нет")]),
        "by_competitor": [{"name": n, "posts": c} for n, c in sorted(by_comp.items(), key=lambda x: -x[1])],
        "top_post_ids": [r.GlobalPost.id for r in top],
        "own_post_ids": [r.GlobalPost.id for r in sorted(
            (r for r in rs if r.Source.role == SourceRole.own), key=lambda r: r.GlobalPost.published_at,
            reverse=True)[:3]],
        "subtopics": await subtopics(session, org_id, topic),
        "related": await related_topics(session, org_id, topic, since),
    }


class GapExplanation(BaseModel):
    why: str
    evidence: list[str] = []
    competitors: list[str] = []
    formats: list[str] = []
    how_to_cover: list[dict] = []
    risks: str = ""


async def explain(session: AsyncSession, org_id: int, topic: str, days: int, router: AIRouter | None = None) -> dict:
    d = await detail(session, org_id, topic, days)
    if d is None:
        raise LookupError(topic)
    posts = (await session.execute(select(GlobalPost, Source).join(Source, and_(
        Source.global_source_id == GlobalPost.global_source_id, Source.organization_id == org_id))
        .where(GlobalPost.id.in_(d["top_post_ids"]), dedupe.not_hidden(org_id)))).all()
    names = await stats.competitor_names(session, org_id)
    user = json.dumps({
        "тема": topic, "период_дней": days,
        "статистика": {k: d[k] for k in ("own", "competitor", "market", "market_total", "share_own", "share_market",
                                         "gap", "trend_pp", "saturation_per_week", "competitors", "median_er")},
        "всего_постов": d["totals"], "под_темы": [{"название": s["label"], "постов": s["size"], "у_клиента": s["own"]}
                                                 for s in d["subtopics"]],
        "форматы_рынка": d["formats_market"], "типы_контента": d["content_types"], "хуки": d["hooks"],
        "конкуренты": d["by_competitor"],
        "лучшие_публикации": [{"кто": names.get(s.competitor_id) or s.name or s.role.value,
                               "overperformance": p.overperformance, "формат": p.media_type,
                               "текст": "\n".join(filter(None, [p.title, (p.text or "")[:500]]))} for p, s in posts],
    }, ensure_ascii=False, default=str)
    tax = await taxonomy.get(session, org_id)
    system = prompts.load("topic/gap").replace("{niche}", tax.niche).replace("{topic}", topic)
    data = await (router or AIRouter(session)).run("analyze", system, user, org_id=org_id, operation="explain_gap",
                                                   schema=GapExplanation, max_tokens=1800)
    stmt = insert(TopicInsight).values(organization_id=org_id, topic=topic, days=days, data=data,
                                       model=model_for("analyze"), created_at=utcnow())
    await session.execute(stmt.on_conflict_do_update(
        constraint="uq_topic_insights_org_topic",
        set_={k: stmt.excluded[k] for k in ("days", "data", "model", "created_at")}))
    await session.commit()
    return data


async def insights(session: AsyncSession, org_id: int) -> dict[str, TopicInsight]:
    rows = (await session.execute(select(TopicInsight).where(TopicInsight.organization_id == org_id)
                                  .execution_options(populate_existing=True))).scalars()
    return {i.topic: i for i in rows}
