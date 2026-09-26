"""Дубли между источниками: один и тот же текст или статья в разных каналах (репост, кросспостинг, RSS + сайт).

Дубль помечается `duplicate_of_id` → самый ранний экземпляр. Дубли не размечаются и не попадают в эмбеддинги,
чтобы не искажать доли тем и не платить дважды.
1) точные: совпал content_hash (текст от 80 символов) или canonical_url;
2) смысловые: косинусное расстояние эмбеддингов ≤ semantic_dup_distance в окне ±3 дня."""
from datetime import timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import GlobalPost, GlobalSource, PostEmbedding

MIN_HASH_TEXT = 80
WINDOW = timedelta(days=3)

EXACT_SQL = text("""
UPDATE global_posts p SET duplicate_of_id = (
    SELECT min(o.id) FROM global_posts o
    WHERE o.id < p.id AND o.global_source_id <> p.global_source_id AND o.duplicate_of_id IS NULL
      AND ((o.content_hash = p.content_hash AND length(p.text) >= :min_text)
           OR (p.canonical_url IS NOT NULL AND o.canonical_url = p.canonical_url))
)
WHERE p.global_source_id = :gs AND p.duplicate_of_id IS NULL
RETURNING p.duplicate_of_id
""")


async def exact(session: AsyncSession, gs: GlobalSource) -> int:
    rows = (await session.execute(EXACT_SQL, {"gs": gs.id, "min_text": MIN_HASH_TEXT})).scalars().all()
    await session.commit()
    return sum(1 for r in rows if r is not None)


async def semantic(session: AsyncSession, gs: GlobalSource, post_ids: list[int]) -> int:
    """Для только что векторизованных постов ищем ближайший пост другого источника рядом по дате."""
    found = 0
    for pid in post_ids:
        post, vec = (await session.execute(
            select(GlobalPost, PostEmbedding.embedding).join(PostEmbedding, PostEmbedding.post_id == GlobalPost.id)
            .where(GlobalPost.id == pid))).one()
        if post.published_at is None or post.duplicate_of_id is not None:
            continue
        distance = PostEmbedding.embedding.cosine_distance(vec)
        match = (await session.execute(
            select(GlobalPost.id, distance.label("d")).join(PostEmbedding, PostEmbedding.post_id == GlobalPost.id)
            .where(GlobalPost.global_source_id != gs.id, GlobalPost.duplicate_of_id.is_(None),
                   GlobalPost.published_at.between(post.published_at - WINDOW, post.published_at + WINDOW),
                   GlobalPost.id < post.id)  # дубль — более поздний экземпляр
            .order_by(distance).limit(1))).first()
        if match and match.d <= settings.semantic_dup_distance:
            post.duplicate_of_id = match.id
            found += 1
    await session.commit()
    return found
