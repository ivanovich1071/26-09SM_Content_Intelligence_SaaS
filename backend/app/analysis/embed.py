"""Эмбеддинги постов для семантического поиска (EPIC 5) и кластеров тем (EPIC 6). Один вектор на пост на всех клиентов;
расход записывается организации, чья задача его посчитала."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embeddings import EmbeddingProvider
from app.analysis.classify import MIN_TEXT
from app.billing import quotas, usage
from app.core.config import settings
from app.models import GlobalPost, GlobalSource, LLMRequest, PostEmbedding

BATCH = 64
MAX_CHARS = 2000


def embed_text(p: GlobalPost) -> str:
    return "\n".join(filter(None, [p.title, p.text]))[:MAX_CHARS]


async def pending(session: AsyncSession, gs: GlobalSource) -> list[GlobalPost]:
    rows = (await session.execute(
        select(GlobalPost).outerjoin(PostEmbedding, PostEmbedding.post_id == GlobalPost.id)
        .where(GlobalPost.global_source_id == gs.id, PostEmbedding.post_id.is_(None))
        .order_by(GlobalPost.published_at.desc().nulls_last()).limit(settings.embed_max_posts_per_job))).scalars()
    return [p for p in rows if len(embed_text(p).strip()) >= MIN_TEXT]


async def embed_source(session: AsyncSession, gs: GlobalSource, provider: EmbeddingProvider, *, org_id: int,
                       job_id: int | None = None) -> list[int]:
    """→ id постов, получивших эмбеддинг. Ошибки провайдера пробрасываются (EmbeddingError) — вызывающий решает."""
    posts = await pending(session, gs)
    done: list[int] = []
    for start in range(0, len(posts), BATCH):
        await quotas.check(session, org_id, "ai_cost_usd_month", amount=0)
        batch = posts[start:start + BATCH]
        result = await provider.embed([embed_text(p) for p in batch])
        for p, vec in zip(batch, result.vectors, strict=True):
            session.add(PostEmbedding(post_id=p.id, model=result.model, embedding=vec))
        cost = result.cost_usd
        session.add(LLMRequest(organization_id=org_id, task="embed", operation="embed_posts", provider=provider.name,
                               model=result.model, input_tokens=result.usage.get("prompt_tokens"), cost_usd=cost,
                               ok=True, job_id=job_id))
        if cost:
            await usage.record(session, org_id, "ai_cost_usd", "embed_posts", cost, provider=provider.name,
                               model=result.model, input_tokens=result.usage.get("prompt_tokens"), cost_usd=cost,
                               job_id=job_id, commit=False)
        await session.commit()
        done.extend(p.id for p in batch)
    return done


async def embed_query(session: AsyncSession, provider: EmbeddingProvider, text: str, *, org_id: int) -> list[float]:
    """Вектор поискового запроса для семантического поиска в Ленте. Расход — на организацию."""
    await quotas.check(session, org_id, "ai_cost_usd_month", amount=0)
    result = await provider.embed([text[:MAX_CHARS]])
    cost = result.cost_usd
    session.add(LLMRequest(organization_id=org_id, task="embed", operation="search_query", provider=provider.name,
                           model=result.model, input_tokens=result.usage.get("prompt_tokens"), cost_usd=cost,
                           ok=True))
    if cost:
        await usage.record(session, org_id, "ai_cost_usd", "search_query", cost, provider=provider.name,
                           model=result.model, cost_usd=cost, commit=False)
    await session.commit()
    return result.vectors[0]
