"""Задача cluster_topics: под-темы внутри каждой темы таксономии.

Посты темы за 90 дней (с эмбеддингами) → HDBSCAN по нормированным векторам (евклидово расстояние на единичной
сфере монотонно косинусному) → типичные посты каждой группы (ближайшие к центру) → названия от модели.
Без модели группы получают название из частых слов, чтобы дерево тем работало и так."""
import json
import re
from collections import Counter, defaultdict
from datetime import timedelta

import numpy as np
from pydantic import BaseModel
from sklearn.cluster import HDBSCAN
from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import prompts
from app.ai.openrouter import AIError
from app.ai.router import AIRouter
from app.analysis import dedupe, taxonomy
from app.billing.quotas import QuotaExceeded
from app.core.config import settings
from app.core.db import utcnow
from app.jobs.service import set_status
from app.models import GlobalPost, Job, JobStatus, PostAnalysis, PostEmbedding, Source, TopicCluster, TopicClusterPost

WINDOW = timedelta(days=90)
MIN_TOPIC_POSTS = 12      # меньше — делить тему не на что
MAX_CLUSTERS = 8          # на тему; самые крупные
SAMPLES = 6
MAX_POSTS = 5000
STOP = set("и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне было вот "
           "от меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до вас нибудь опять "
           "уж вам ведь там потом себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб без "
           "будто чего раз тоже себе под будет ж тогда кто этот того потому этого какой совсем ним здесь этом один "
           "почти мой тем чтобы нее сейчас были куда зачем всех никогда можно при наконец два об другой хоть после "
           "над больше тот через эти нас про всего них какая много разве три эту моя впрочем хорошо свою этой перед "
           "иногда лучше чуть том нельзя такой им более всегда конечно всю между это наш наши ваш свой".split())


class NotEnoughData(Exception):
    user_facing = True


class Naming(BaseModel):
    clusters: list[dict]


def split(vectors: np.ndarray) -> np.ndarray:
    """→ метка группы для каждого поста (-1 — не попал ни в одну)."""
    x = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
    min_size = max(4, len(x) // 15)
    return HDBSCAN(min_cluster_size=min_size, min_samples=2, copy=True).fit_predict(x)


def _doc_freq(texts: list[str]) -> Counter:
    return Counter(w for t in texts for w in set(re.findall(r"[a-zа-яё]{4,}", t.lower())) if w not in STOP)


def keywords(texts: list[str], top: int = 5, others: list[str] | None = None) -> list[str]:
    """Отличительные слова группы: доля постов группы со словом минус доля в остальных группах темы.
    Порядок детерминирован (при равенстве — по алфавиту), общие для всех групп слова уходят вниз."""
    own = _doc_freq(texts)
    rest = _doc_freq(others or [])
    n, m = max(len(texts), 1), max(len(others or []), 1)
    scored = sorted(own, key=lambda w: (-(own[w] / n - rest[w] / m), -own[w], w))
    return [w for w in scored if own[w] / n - rest[w] / m > 0][:top] or sorted(own, key=lambda w: (-own[w], w))[:top]


async def _load(session: AsyncSession, org_id: int) -> dict[str, list[tuple[int, str, list[float]]]]:
    stmt = (select(GlobalPost.id, GlobalPost.title, GlobalPost.text, PostAnalysis.topic, PostEmbedding.embedding)
            .join(PostAnalysis, and_(PostAnalysis.post_id == GlobalPost.id, PostAnalysis.organization_id == org_id,
                                     PostAnalysis.error.is_(None)))
            .join(PostEmbedding, PostEmbedding.post_id == GlobalPost.id)
            .join(Source, and_(Source.global_source_id == GlobalPost.global_source_id,
                               Source.organization_id == org_id, Source.enabled.is_(True)))
            .where(GlobalPost.published_at >= utcnow() - WINDOW, dedupe.not_hidden(org_id))
            .order_by(GlobalPost.published_at.desc()).limit(MAX_POSTS))
    by_topic: dict[str, list] = defaultdict(list)
    for pid, title, text, topic, emb in (await session.execute(stmt)).all():
        if topic and topic != taxonomy.OTHER_TOPIC:
            by_topic[topic].append((pid, "\n".join(filter(None, [title, text])), list(emb)))
    return by_topic


async def _name(router: AIRouter | None, tax, topic: str, groups: dict[int, list[str]], org_id: int,
                job_id: int) -> dict[int, dict]:
    def others(k: int) -> list[str]:
        return [t for j, v in groups.items() if j != k for t in v]
    fallback = {k: {"label": ", ".join(keywords(v, 3, others(k))) or f"{topic} — группа {k + 1}",
                    "description": None, "keywords": keywords(v, 5, others(k))} for k, v in groups.items()}
    if router is None:
        return fallback
    user = json.dumps([{"id": k, "примеры": [t[:400] for t in v]} for k, v in groups.items()], ensure_ascii=False)
    system = prompts.load("topic/naming").replace("{niche}", tax.niche).replace("{topic}", topic)
    data = await router.run("analyze", system, user, org_id=org_id, operation="name_subtopics", schema=Naming,
                            job_id=job_id, max_tokens=150 * len(groups) + 200)
    out = dict(fallback)
    for c in data["clusters"]:
        try:
            k = int(c.get("id"))
        except (TypeError, ValueError):
            continue
        if k in out and str(c.get("label") or "").strip():
            out[k] = {"label": str(c["label"]).strip()[:200], "description": (c.get("description") or None),
                      "keywords": [str(w) for w in (c.get("keywords") or [])][:8] or out[k]["keywords"]}
    return out


async def build(session: AsyncSession, job: Job, router: AIRouter | None) -> dict:
    org_id = job.organization_id
    by_topic = await _load(session, org_id)
    topics = {t: rows for t, rows in by_topic.items() if len(rows) >= MIN_TOPIC_POSTS}
    if not topics:
        raise NotEnoughData(f"Недостаточно данных: ни в одной теме нет {MIN_TOPIC_POSTS} размеченных постов "
                            f"с эмбеддингами за {WINDOW.days} дней")
    tax = await taxonomy.get(session, org_id)
    created: list[tuple[str, dict, list[int]]] = []
    naming_note = None
    for i, (topic, rows) in enumerate(sorted(topics.items())):
        vectors = np.array([r[2] for r in rows], dtype=float)
        labels = split(vectors)
        groups = [int(k) for k, _ in Counter(labels[labels >= 0].tolist()).most_common(MAX_CLUSTERS)]
        if len(groups) < 2:
            continue  # тема однородна — делить нечего
        members, samples = {}, {}
        for k in groups:
            idx = np.where(labels == k)[0]
            centre = vectors[idx].mean(axis=0)
            dist = np.linalg.norm(vectors[idx] - centre, axis=1)
            members[k] = [rows[j][0] for j in idx]
            samples[k] = [rows[j][1] for j in idx[np.argsort(dist)[:SAMPLES]]]
        try:
            names = await _name(router, tax, topic, samples, org_id, job.id)
        except (AIError, QuotaExceeded) as e:
            naming_note = f"Названия под-тем подобраны по частым словам: модель недоступна ({e})"
            router = None
            names = await _name(None, tax, topic, samples, org_id, job.id)
        created.extend((topic, names[k], members[k]) for k in groups)
        await set_status(session, job, JobStatus.analyzing, progress=10 + int(80 * (i + 1) / len(topics)))

    await session.execute(delete(TopicCluster).where(TopicCluster.organization_id == org_id))
    for topic, name, post_ids in created:
        cluster = TopicCluster(organization_id=org_id, topic=topic, label=name["label"],
                               description=name["description"], keywords=name["keywords"], size=len(post_ids),
                               job_id=job.id)
        session.add(cluster)
        await session.flush()
        session.add_all(TopicClusterPost(cluster_id=cluster.id, post_id=pid) for pid in post_ids)
    await session.commit()
    out = {"topics_split": len({t for t, _, _ in created}), "subtopics": len(created),
           "topics_checked": len(topics)}
    if naming_note:
        out["message"] = naming_note
    return out


async def handle_cluster_topics(session: AsyncSession, job: Job, *, router: AIRouter | None = None) -> dict:
    await set_status(session, job, JobStatus.analyzing, progress=5)
    if router is None and settings.openrouter_api_key:
        router = AIRouter(session)
    return await build(session, job, router)


async def schedule_due(session: AsyncSession, pool) -> int:
    """Организации с данными для под-тем, у которых последний пересчёт старше недели (или не было)."""
    from sqlalchemy import func

    from app.jobs.service import create_job, enqueue
    candidates = (await session.execute(
        select(PostAnalysis.organization_id).join(PostEmbedding, PostEmbedding.post_id == PostAnalysis.post_id)
        .where(PostAnalysis.error.is_(None), PostAnalysis.analyzed_at >= utcnow() - WINDOW)
        .group_by(PostAnalysis.organization_id).having(func.count() >= MIN_TOPIC_POSTS))).scalars().all()
    created = 0
    for org_id in candidates:
        last = (await session.execute(select(Job).where(Job.organization_id == org_id, Job.kind == "cluster_topics")
                                      .order_by(Job.id.desc()).limit(1))).scalar_one_or_none()
        if last and (last.status not in (JobStatus.completed, JobStatus.failed, JobStatus.cancelled)
                     or last.created_at > utcnow() - timedelta(days=7)):
            continue
        await enqueue(pool, await create_job(session, org_id, "cluster_topics"))
        created += 1
    return created
