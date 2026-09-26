"""Цифры аудита — всё считает код: статистика аудируемых каналов, benchmark рынка организации и пробелы по темам.

Benchmark: «ваш показатель vs медиана рынка vs сильные публикации рынка (верхний квартиль по ER)». Если постов
рынка меньше порога — сравнение не выводится вовсе: «Недостаточно данных», benchmark не выдумывается."""
from collections import Counter, namedtuple
from datetime import timedelta
from statistics import median, quantiles

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis import dedupe
from app.analysis.taxonomy import OTHER_TOPIC
from app.competitors.stats import flag_pct, summarize
from app.core.config import settings
from app.core.db import utcnow
from app.models import GlobalPost, GlobalSource, PostAnalysis, Source, SourceRole

Row = namedtuple("Row", "GlobalPost PostAnalysis GlobalSource")
MARKET_ROLES = (SourceRole.competitor, SourceRole.market)
GAP_MIN_PP, GAP_MIN_MARKET, GAP_MIN_OWN = 5.0, 3, 5
SHARE_METRICS = [
    ("cta_share", "Посты с призывом к действию"),
    ("hook_share", "Посты с цепляющим началом"),
    ("case_share", "Посты с кейсами"),
    ("numbers_share", "Посты с цифрами"),
    ("lead_magnet_share", "Посты с лид-магнитом"),
]


async def own_rows(session: AsyncSession, org_id: int, gs_ids: list[int], days: int) -> list[Row]:
    """Посты аудируемых каналов. Дубли не скрываем: репост чужого поста — тоже контент компании."""
    if not gs_ids:
        return []
    stmt = (select(GlobalPost, PostAnalysis, GlobalSource).join(GlobalSource)
            .outerjoin(PostAnalysis, and_(PostAnalysis.post_id == GlobalPost.id,
                                          PostAnalysis.organization_id == org_id))
            .where(GlobalSource.id.in_(gs_ids), GlobalPost.published_at >= utcnow() - timedelta(days=days))
            .order_by(GlobalPost.published_at.desc())
            .execution_options(populate_existing=True))  # разметка только что обновлена upsert-ом в обход ORM
    return [Row(*r) for r in (await session.execute(stmt)).all()]


def label(r: Row) -> PostAnalysis | None:
    return r.PostAnalysis if r.PostAnalysis and not r.PostAnalysis.error else None


def channel_stats(rows: list[Row], days: int) -> dict:
    st = {k: v for k, v in summarize(rows, days).items() if k != "weekly"}
    last = max((r.GlobalPost.published_at for r in rows), default=None)
    st["days_since_last_post"] = (utcnow() - last).days if last else None
    return st


def _q3(values: list[float]) -> float | None:
    if len(values) < 4:
        return None
    return round(quantiles(values, n=4)[2], 2)


async def market(session: AsyncSession, org_id: int, exclude_gs: list[int], days: int) -> dict:
    stmt = (select(GlobalPost, PostAnalysis, Source)
            .join(Source, and_(Source.global_source_id == GlobalPost.global_source_id,
                               Source.organization_id == org_id, Source.enabled.is_(True),
                               Source.role.in_(MARKET_ROLES)))
            .outerjoin(PostAnalysis, and_(PostAnalysis.post_id == GlobalPost.id,
                                          PostAnalysis.organization_id == org_id))
            .where(GlobalPost.published_at >= utcnow() - timedelta(days=days), dedupe.not_hidden(org_id),
                   GlobalPost.global_source_id.not_in(exclude_gs or [0])))
    rows = (await session.execute(stmt)).all()
    sources = {r.Source.id for r in rows}
    need = settings.audit_min_market_posts
    base = {"posts": len(rows), "sources": len(sources), "min_posts": need}
    if len(rows) < need:
        hint = ("Добавьте конкурентов и отраслевые каналы в «Конкуренты» и «Источники»." if not sources else
                "Добавьте ещё источников рынка или дождитесь сбора.")
        return {**base, "enough": False,
                "message": f"Недостаточно данных для надёжного рыночного сравнения: {len(rows)} публикаций рынка "
                           f"за {days} дней, нужно от {need}. {hint}"}

    ers = [r.GlobalPost.er for r in rows if r.GlobalPost.er is not None]
    q3 = _q3(ers)
    labels = [(r.GlobalPost, a) for r in rows if (a := r.PostAnalysis) and not a.error]
    strong = [a for p, a in labels if q3 is not None and p.er is not None and p.er >= q3]
    per_source = Counter(r.Source.id for r in rows)
    ppw = [n / (days / 7) for n in per_source.values()]
    return {**base, "enough": True, "analyzed": len(labels), "strong_posts": len(strong),
            "posts_per_week": round(median(ppw), 1), "posts_per_week_top": _q3(ppw),
            "median_er": round(median(ers), 2) if ers else None, "median_er_top": q3,
            **{k: flag_pct([_flag(a, k) for _, a in labels]) for k, _ in SHARE_METRICS},
            **{f"{k}_top": flag_pct([_flag(a, k) for a in strong]) if strong else None for k, _ in SHARE_METRICS},
            "topics": Counter((a.topic or OTHER_TOPIC) for _, a in labels)}


def _flag(a: PostAnalysis, key: str) -> bool | None:
    return {"cta_share": a.cta_type != "нет", "hook_share": a.hook_type != "нет", "case_share": a.has_case,
            "numbers_share": a.has_numbers, "lead_magnet_share": a.has_lead_magnet}[key]


def comparison(own: dict, mk: dict) -> list[dict]:
    """Строки таблицы benchmark. Пусто, если данных рынка мало."""
    if not mk.get("enough"):
        return []
    rows = [{"key": "posts_per_week", "label": "Публикаций в неделю", "own": own.get("posts_per_week"),
             "market": mk["posts_per_week"], "top": mk["posts_per_week_top"]},
            {"key": "median_er", "label": "Вовлечённость (ER), медиана", "own": own.get("median_er"),
             "market": mk["median_er"], "top": mk["median_er_top"]}]
    rows += [{"key": k, "label": label, "own": own.get(k) if own.get("analyzed") else None,
              "market": mk.get(k), "top": mk.get(f"{k}_top")} for k, label in SHARE_METRICS]
    return rows


def gaps(rows: list[Row], mk: dict) -> list[dict]:
    """Темы, где доля рынка выше доли компании: рынок об этом пишет, компания — нет."""
    own = Counter((a.topic or OTHER_TOPIC) for r in rows if (a := label(r)))
    own_total, market_counts = sum(own.values()), mk.get("topics") or Counter()
    market_total = sum(market_counts.values())
    if not mk.get("enough") or own_total < GAP_MIN_OWN or not market_total:
        return []
    out = []
    for topic, n in market_counts.items():
        share_market = round(100 * n / market_total, 1)
        share_own = round(100 * own[topic] / own_total, 1)
        gap = round(share_market - share_own, 1)
        if topic != OTHER_TOPIC and n >= GAP_MIN_MARKET and gap >= GAP_MIN_PP:
            out.append({"topic": topic, "share_market": share_market, "share_own": share_own, "gap": gap,
                        "market_posts": n, "own_posts": own[topic]})
    return sorted(out, key=lambda g: -g["gap"])[:8]


def sample(rows: list[Row], top_ids: list[int], limit: int = 20) -> list[Row]:
    """Для модели: лучшие по overperformance + свежие посты с текстом."""
    by_id = {r.GlobalPost.id: r for r in rows}
    chosen = [by_id[i] for i in top_ids if i in by_id][:8]
    for r in rows:
        if len(chosen) >= limit:
            break
        if r not in chosen and (r.GlobalPost.text or r.GlobalPost.title):
            chosen.append(r)
    return chosen
