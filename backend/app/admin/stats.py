"""Запросы админки: сводка, организации, расходы LLM, задачи, ошибки, провайдеры.

Расход считается по llm_requests (все вызовы модели, включая неудачные); квоты — по usage_events."""
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import quotas
from app.billing.plans import DEFAULT_PLAN
from app.core.db import utcnow
from app.models import (
    FINAL_STATUSES,
    AdminAction,
    Competitor,
    Digest,
    GlobalSource,
    Job,
    JobStatus,
    LLMRequest,
    Membership,
    Organization,
    Plan,
    Role,
    Source,
    SourceStatus,
    Subscription,
    UsageEvent,
    User,
    Website,
)

ACTIVE_JOBS = [s for s in JobStatus if s not in FINAL_STATUSES]
STUCK_MINUTES = 15  # queued дольше — воркер не забрал задачу (не запущен или нет Redis)
# Повтор безопасен: задача сама пересчитывает состояние. Аудит и генерация привязаны к своей записи — повтор из UI
RETRYABLE = {"ping", "sync_source", "analyze_source", "profile_competitor", "cluster_topics", "crawl_website",
             "build_opportunities", "generate_digest"}


def is_system(org: Organization) -> bool:
    return org.slug.startswith("_")  # служебные организации (публичные аудиты) — «_» нет в пользовательских slug


def prev_month_start(month: datetime) -> datetime:
    return (month - timedelta(days=1)).replace(day=1)


def money(v) -> float:
    return round(float(v or 0), 4)


def page_of(stmt: Select, page: int, per_page: int) -> Select:
    return stmt.limit(per_page).offset((max(page, 1) - 1) * per_page)


async def count(session: AsyncSession, stmt: Select) -> int:
    return (await session.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()


async def cost_by_day(session: AsyncSession, since: datetime, org_id: int | None = None) -> list[dict]:
    day = func.date_trunc("day", LLMRequest.at).label("day")
    stmt = (select(day, func.count(), func.coalesce(func.sum(LLMRequest.cost_usd), 0),
                   func.count().filter(LLMRequest.ok.is_(False)))
            .where(LLMRequest.at >= since).group_by(day).order_by(day))
    if org_id is not None:
        stmt = stmt.where(LLMRequest.organization_id == org_id)
    rows = {d.date(): (n, c, e) for d, n, c, e in await session.execute(stmt)}
    out, d = [], since.date()
    while d <= utcnow().date():
        n, c, e = rows.get(d, (0, 0, 0))
        out.append({"date": d.isoformat(), "calls": n, "cost_usd": money(c), "errors": e})
        d += timedelta(days=1)
    return out


async def grouped_cost(session: AsyncSession, column, since: datetime, *, org_id: int | None = None,
                       limit: int = 20) -> list[dict]:
    stmt = (select(column, func.count(), func.coalesce(func.sum(LLMRequest.cost_usd), 0),
                   func.count().filter(LLMRequest.ok.is_(False)),
                   func.coalesce(func.sum(LLMRequest.input_tokens), 0),
                   func.coalesce(func.sum(LLMRequest.output_tokens), 0))
            .where(LLMRequest.at >= since).group_by(column)
            .order_by(func.coalesce(func.sum(LLMRequest.cost_usd), 0).desc(), func.count().desc()).limit(limit))
    if org_id is not None:
        stmt = stmt.where(LLMRequest.organization_id == org_id)
    return [{"key": k, "calls": n, "cost_usd": money(c), "errors": e, "input_tokens": i, "output_tokens": o}
            for k, n, c, e, i, o in await session.execute(stmt)]


async def org_names(session: AsyncSession, ids) -> dict[int, str]:
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    return dict((await session.execute(select(Organization.id, Organization.name)
                                       .where(Organization.id.in_(ids)))).all())


# --- сводка ---

async def overview(session: AsyncSession, days: int) -> dict:
    now, month = utcnow(), quotas.month_start()
    prev = prev_month_start(month)
    users = (await session.execute(select(
        func.count(), func.count().filter(User.is_active.is_(True)),
        func.count().filter(User.created_at >= now - timedelta(days=7)),
        func.count().filter(User.is_superadmin.is_(True)),
        func.count().filter(User.last_seen_at >= now - timedelta(days=7))))).one()

    subs = (await session.execute(select(Organization, Subscription)
                                  .outerjoin(Subscription, Subscription.organization_id == Organization.id))).all()
    prices = dict((await session.execute(select(Plan.code, Plan.price_month_usd))).all())
    by_plan: Counter = Counter()
    paying, mrr, unpriced, system, new = 0, 0.0, 0, 0, 0
    for org, sub in subs:
        if is_system(org):
            system += 1
            continue
        new += org.created_at >= now - timedelta(days=7)
        plan = sub.plan_code if quotas.is_active(sub) else DEFAULT_PLAN
        by_plan[plan] += 1
        if plan != DEFAULT_PLAN:
            paying += 1
            if prices.get(plan) is None:
                unpriced += 1
            else:
                mrr += float(prices[plan])

    ai = (await session.execute(select(
        func.coalesce(func.sum(LLMRequest.cost_usd).filter(LLMRequest.at >= month), 0),
        func.coalesce(func.sum(LLMRequest.cost_usd).filter(LLMRequest.at >= prev, LLMRequest.at < month), 0),
        func.count().filter(LLMRequest.at >= month),
        func.count().filter(LLMRequest.at >= now - timedelta(hours=24), LLMRequest.ok.is_(False)),
        func.count().filter(LLMRequest.at >= now - timedelta(hours=24))).where(LLMRequest.at >= prev))).one()

    jobs = (await session.execute(select(
        func.count().filter(Job.status.in_(ACTIVE_JOBS)),
        func.count().filter(Job.status == JobStatus.failed, Job.finished_at >= now - timedelta(hours=24)),
        func.count().filter(Job.status == JobStatus.queued, Job.created_at < now - timedelta(minutes=STUCK_MINUTES)),
        func.count().filter(Job.created_at >= now - timedelta(hours=24))))).one()

    top = await grouped_cost(session, LLMRequest.organization_id, month, limit=5)
    names = await org_names(session, [t["key"] for t in top])
    return {
        "users": {"total": users[0], "active": users[1], "new_7d": users[2], "superadmins": users[3],
                  "seen_7d": users[4]},
        "organizations": {"total": len(subs) - system, "new_7d": new, "system": system, "paying": paying,
                          "by_plan": dict(by_plan)},
        "mrr_usd": round(mrr, 2), "unpriced_paying": unpriced,
        "ai": {"cost_month": money(ai[0]), "cost_prev_month": money(ai[1]), "calls_month": ai[2],
               "errors_24h": ai[3], "calls_24h": ai[4], "month_start": month.isoformat(),
               "by_day": await cost_by_day(session, (now - timedelta(days=days - 1)).replace(
                   hour=0, minute=0, second=0, microsecond=0))},
        "jobs": {"active": jobs[0], "failed_24h": jobs[1], "stuck": jobs[2], "created_24h": jobs[3]},
        "top_orgs": [{"id": t["key"], "name": names.get(t["key"], "—"), "cost_usd": t["cost_usd"],
                      "calls": t["calls"]} for t in top],
    }


# --- организации ---

async def _count_by_org(session: AsyncSession, model, ids: list[int], *extra) -> dict[int, int]:
    stmt = (select(model.organization_id, func.count()).where(model.organization_id.in_(ids), *extra)
            .group_by(model.organization_id))
    return dict((await session.execute(stmt)).all())


async def organizations(session: AsyncSession, *, q: str = "", plan: str = "", sort: str = "created",
                        page: int = 1, per_page: int = 50) -> dict:
    month = quotas.month_start()
    cost = (select(LLMRequest.organization_id.label("org_id"), func.sum(LLMRequest.cost_usd).label("cost"))
            .where(LLMRequest.at >= month).group_by(LLMRequest.organization_id).subquery())
    stmt = (select(Organization, Subscription, func.coalesce(cost.c.cost, 0))
            .outerjoin(Subscription, Subscription.organization_id == Organization.id)
            .outerjoin(cost, cost.c.org_id == Organization.id))
    if q.strip():
        like = f"%{q.strip()}%"
        members = (select(Membership.organization_id).join(User, User.id == Membership.user_id)
                   .where(User.email.ilike(like)))
        stmt = stmt.where(or_(Organization.name.ilike(like), Organization.slug.ilike(like),
                              Organization.id.in_(members)))
    if plan:
        stmt = stmt.where(Subscription.plan_code == plan)
    order = {"name": [Organization.name], "cost": [func.coalesce(cost.c.cost, 0).desc(), Organization.id.desc()]}
    stmt = stmt.order_by(*order.get(sort, [Organization.id.desc()]))
    total = await count(session, stmt)
    rows = (await session.execute(page_of(stmt, page, per_page))).all()
    ids = [o.id for o, _, _ in rows]
    members = await _count_by_org(session, Membership, ids)
    sources = await _count_by_org(session, Source, ids)
    competitors = await _count_by_org(session, Competitor, ids)
    last_job = dict((await session.execute(select(Job.organization_id, func.max(Job.created_at))
                                           .where(Job.organization_id.in_(ids)).group_by(Job.organization_id))).all())
    owners = dict((await session.execute(
        select(Membership.organization_id, func.min(User.email)).join(User, User.id == Membership.user_id)
        .where(Membership.organization_id.in_(ids), Membership.role == Role.owner)
        .group_by(Membership.organization_id))).all())
    usage = (await session.execute(
        select(UsageEvent.organization_id, UsageEvent.metric, func.sum(UsageEvent.quantity))
        .where(UsageEvent.organization_id.in_(ids), UsageEvent.at >= month,
               UsageEvent.metric.in_(("audits", "generations")))
        .group_by(UsageEvent.organization_id, UsageEvent.metric))).all()
    used = {(o, m): float(v) for o, m, v in usage}
    items = []
    for org, sub, c in rows:
        active = quotas.is_active(sub)
        items.append({
            "id": org.id, "name": org.name, "slug": org.slug, "system": is_system(org), "created_at": org.created_at,
            "owner_email": owners.get(org.id),
            "plan": sub.plan_code if sub else DEFAULT_PLAN, "effective_plan": sub.plan_code if active else DEFAULT_PLAN,
            "status": sub.status if sub else None, "current_period_end": sub.current_period_end if sub else None,
            "has_override": bool(sub and sub.limits_override),
            "members": members.get(org.id, 0), "sources": sources.get(org.id, 0),
            "competitors": competitors.get(org.id, 0), "ai_cost_month": money(c),
            "audits_month": used.get((org.id, "audits"), 0), "generations_month": used.get((org.id, "generations"), 0),
            "last_job_at": last_job.get(org.id)})
    return {"items": items, "total": total, "page": page, "per_page": per_page}


async def subscription_out(session: AsyncSession, org_id: int) -> dict:
    sub = (await session.execute(select(Subscription).where(Subscription.organization_id == org_id))
           ).scalar_one_or_none()
    plan, limits = await quotas.plan_limits(session, org_id)
    return {"plan_code": sub.plan_code if sub else DEFAULT_PLAN, "status": sub.status if sub else None,
            "current_period_start": sub.current_period_start if sub else None,
            "current_period_end": sub.current_period_end if sub else None,
            "limits_override": sub.limits_override if sub else None, "note": sub.note if sub else None,
            "effective_plan": plan, "limits": limits, "active": quotas.is_active(sub)}


async def organization(session: AsyncSession, org: Organization) -> dict:
    month, since = quotas.month_start(), utcnow() - timedelta(days=29)
    members = (await session.execute(
        select(Membership, User).join(User, User.id == Membership.user_id)
        .where(Membership.organization_id == org.id).order_by(Membership.id))).all()
    jobs = (await session.execute(select(Job).where(Job.organization_id == org.id)
                                  .order_by(Job.id.desc()).limit(20))).scalars().all()
    errors = (await session.execute(
        select(LLMRequest).where(LLMRequest.organization_id == org.id, LLMRequest.ok.is_(False))
        .order_by(LLMRequest.id.desc()).limit(10))).scalars().all()
    actions = (await session.execute(
        select(AdminAction, User.email).outerjoin(User, User.id == AdminAction.admin_id)
        .where(AdminAction.organization_id == org.id).order_by(AdminAction.id.desc()).limit(20))).all()
    ids = [org.id]
    counts = {
        "members": len(members),
        "sources": (await _count_by_org(session, Source, ids)).get(org.id, 0),
        "competitors": (await _count_by_org(session, Competitor, ids)).get(org.id, 0),
        "websites": (await _count_by_org(session, Website, ids)).get(org.id, 0),
    }
    return {
        "id": org.id, "name": org.name, "slug": org.slug, "system": is_system(org), "created_at": org.created_at,
        "subscription": await subscription_out(session, org.id),
        "used": await quotas.month_usage(session, org.id), "counts": counts,
        "members": [{"id": m.id, "user_id": u.id, "email": u.email, "full_name": u.full_name, "role": m.role,
                     "is_active": u.is_active, "is_superadmin": u.is_superadmin, "last_seen_at": u.last_seen_at}
                    for m, u in members],
        "jobs": [job_out(j) for j in jobs],
        "ai_by_operation": await grouped_cost(session, LLMRequest.operation, month, org_id=org.id),
        "ai_by_model": await grouped_cost(session, LLMRequest.model, month, org_id=org.id),
        "ai_by_day": await cost_by_day(session, since.replace(hour=0, minute=0, second=0, microsecond=0), org.id),
        "llm_errors": [{"at": e.at, "operation": e.operation, "model": e.model, "error": (e.error or "")[:500]}
                       for e in errors],
        "actions": [action_out(a, email) for a, email in actions],
    }


# --- пользователи ---

async def users(session: AsyncSession, *, q: str = "", flag: str = "", page: int = 1, per_page: int = 50) -> dict:
    stmt = select(User)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(User.email.ilike(like), User.full_name.ilike(like)))
    if flag == "superadmin":
        stmt = stmt.where(User.is_superadmin.is_(True))
    elif flag == "blocked":
        stmt = stmt.where(User.is_active.is_(False))
    stmt = stmt.order_by(User.id.desc())
    total = await count(session, stmt)
    rows = (await session.execute(page_of(stmt, page, per_page))).scalars().all()
    orgs = (await session.execute(
        select(Membership.user_id, Organization.id, Organization.name, Membership.role)
        .join(Organization, Organization.id == Membership.organization_id)
        .where(Membership.user_id.in_([u.id for u in rows])).order_by(Membership.id))).all()
    by_user: dict[int, list] = {}
    for uid, oid, name, role in orgs:
        by_user.setdefault(uid, []).append({"id": oid, "name": name, "role": role})
    return {"items": [user_out(u, by_user.get(u.id, [])) for u in rows], "total": total, "page": page,
            "per_page": per_page}


def user_out(u: User, orgs: list[dict]) -> dict:
    return {"id": u.id, "email": u.email, "full_name": u.full_name, "is_active": u.is_active,
            "is_superadmin": u.is_superadmin, "created_at": u.created_at, "last_seen_at": u.last_seen_at,
            "organizations": orgs}


# --- расходы ---

async def costs(session: AsyncSession, days: int) -> dict:
    since = (utcnow() - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    t = (await session.execute(select(
        func.count(), func.count().filter(LLMRequest.ok.is_(False)), func.coalesce(func.sum(LLMRequest.cost_usd), 0),
        func.coalesce(func.sum(LLMRequest.input_tokens), 0), func.coalesce(func.sum(LLMRequest.output_tokens), 0),
        func.avg(LLMRequest.latency_ms)).where(LLMRequest.at >= since))).one()
    by_org = await grouped_cost(session, LLMRequest.organization_id, since)
    names = await org_names(session, [r["key"] for r in by_org])
    for r in by_org:
        r["name"] = names.get(r["key"], "без организации")
    return {
        "since": since.isoformat(), "days": days,
        "totals": {"calls": t[0], "errors": t[1], "cost_usd": money(t[2]), "input_tokens": t[3],
                   "output_tokens": t[4], "avg_latency_ms": round(float(t[5])) if t[5] is not None else None},
        "by_day": await cost_by_day(session, since),
        "by_org": by_org,
        "by_model": await grouped_cost(session, LLMRequest.model, since),
        "by_operation": await grouped_cost(session, LLMRequest.operation, since, limit=40),
        "by_task": await grouped_cost(session, LLMRequest.task, since),
    }


# --- задачи ---

def job_out(j: Job, org_name: str | None = None) -> dict:
    return {"id": j.id, "organization_id": j.organization_id, "organization": org_name, "kind": j.kind,
            "status": j.status, "progress": j.progress, "params": j.params, "error": j.error,
            "created_at": j.created_at, "started_at": j.started_at, "finished_at": j.finished_at,
            "retryable": j.kind in RETRYABLE and j.status in (JobStatus.failed, JobStatus.cancelled)}


async def jobs(session: AsyncSession, *, status: str = "", kind: str = "", org_id: int | None = None,
               page: int = 1, per_page: int = 50) -> dict:
    stmt = select(Job, Organization.name).join(Organization, Organization.id == Job.organization_id)
    if status == "active":
        stmt = stmt.where(Job.status.in_(ACTIVE_JOBS))
    elif status == "stuck":
        stmt = stmt.where(Job.status == JobStatus.queued,
                          Job.created_at < utcnow() - timedelta(minutes=STUCK_MINUTES))
    elif status:
        stmt = stmt.where(Job.status == status)
    if kind:
        stmt = stmt.where(Job.kind == kind)
    if org_id:
        stmt = stmt.where(Job.organization_id == org_id)
    stmt = stmt.order_by(Job.id.desc())
    total = await count(session, stmt)
    rows = (await session.execute(page_of(stmt, page, per_page))).all()
    since = utcnow() - timedelta(hours=24)
    by_status = dict((await session.execute(select(Job.status, func.count()).where(Job.created_at >= since)
                                            .group_by(Job.status))).all())
    kinds = list((await session.execute(select(Job.kind).distinct().order_by(Job.kind))).scalars())
    return {"items": [job_out(j, name) for j, name in rows], "total": total, "page": page, "per_page": per_page,
            "kinds": kinds, "by_status_24h": {str(k): v for k, v in by_status.items()}}


# --- ошибки ---

async def errors(session: AsyncSession, days: int, kind: str = "") -> dict:
    since, items = utcnow() - timedelta(days=days), []
    if kind in ("", "job"):
        for j, name in await session.execute(
                select(Job, Organization.name).join(Organization, Organization.id == Job.organization_id)
                .where(Job.status == JobStatus.failed, Job.finished_at >= since).order_by(Job.id.desc()).limit(100)):
            items.append({"at": j.finished_at, "type": "job", "organization_id": j.organization_id,
                          "organization": name, "title": f"Задача {j.kind} #{j.id}", "message": j.error or "",
                          "ref": {"job_id": j.id, "retryable": j.kind in RETRYABLE}})
    if kind in ("", "llm"):
        rows = (await session.execute(select(LLMRequest).where(LLMRequest.ok.is_(False), LLMRequest.at >= since)
                                      .order_by(LLMRequest.id.desc()).limit(100))).scalars().all()
        names = await org_names(session, [r.organization_id for r in rows])
        for r in rows:
            items.append({"at": r.at, "type": "llm", "organization_id": r.organization_id,
                          "organization": names.get(r.organization_id), "title": f"{r.model} · {r.operation}",
                          "message": r.error or "", "ref": {"job_id": r.job_id}})
    if kind in ("", "source"):
        users_n = (select(Source.global_source_id, func.count().label("n")).group_by(Source.global_source_id)
                   .subquery())
        for gs, n in await session.execute(
                select(GlobalSource, func.coalesce(users_n.c.n, 0))
                .outerjoin(users_n, users_n.c.global_source_id == GlobalSource.id)
                .where(GlobalSource.status.in_((SourceStatus.error, SourceStatus.unavailable)))
                .order_by(GlobalSource.id.desc()).limit(100)):
            items.append({"at": gs.last_synced_at or gs.created_at, "type": "source", "organization_id": None,
                          "organization": f"организаций: {n}", "title": f"{gs.kind.value}: {gs.title or gs.key}",
                          "message": gs.last_error or gs.status.value, "ref": {"url": gs.url}})
    if kind in ("", "website"):
        for w, name in await session.execute(
                select(Website, Organization.name).join(Organization, Organization.id == Website.organization_id)
                .where(Website.status == "error").order_by(Website.id.desc()).limit(100)):
            items.append({"at": w.last_crawled_at or w.created_at, "type": "website",
                          "organization_id": w.organization_id, "organization": name, "title": w.name or w.url,
                          "message": w.last_error or "", "ref": {"url": w.url}})
    if kind in ("", "email"):
        for d, name in await session.execute(
                select(Digest, Organization.name).join(Organization, Organization.id == Digest.organization_id)
                .where(Digest.email_error.is_not(None), Digest.created_at >= since)
                .order_by(Digest.id.desc()).limit(100)):
            items.append({"at": d.created_at, "type": "email", "organization_id": d.organization_id,
                          "organization": name, "title": f"Дайджест #{d.id}: письмо не ушло",
                          "message": d.email_error, "ref": {"digest_id": d.id}})
    items.sort(key=lambda i: i["at"] or since, reverse=True)
    return {"items": items[:300], "counts": dict(Counter(i["type"] for i in items)), "days": days}


# --- провайдеры ---

async def llm_health(session: AsyncSession, hours: int = 24) -> list[dict]:
    since = utcnow() - timedelta(hours=hours)
    p50 = func.percentile_cont(0.5).within_group(LLMRequest.latency_ms)
    rows = await session.execute(
        select(LLMRequest.provider, LLMRequest.model, func.count(), func.count().filter(LLMRequest.ok.is_(False)),
               p50, func.coalesce(func.sum(LLMRequest.cost_usd), 0), func.max(LLMRequest.at),
               func.max(LLMRequest.at).filter(LLMRequest.ok.is_(True)))
        .where(LLMRequest.at >= since).group_by(LLMRequest.provider, LLMRequest.model)
        .order_by(func.count().desc()))
    return [{"provider": p, "model": m, "calls": n, "errors": e, "error_rate": round(e / n, 3) if n else 0,
             "p50_latency_ms": round(lat) if lat is not None else None, "cost_usd": money(c), "last_at": last,
             "last_ok_at": last_ok} for p, m, n, e, lat, c, last, last_ok in rows]


async def connector_health(session: AsyncSession) -> dict[str, dict]:
    rows = await session.execute(
        select(GlobalSource.kind, func.count(), func.count().filter(GlobalSource.status == SourceStatus.error),
               func.count().filter(GlobalSource.status == SourceStatus.unavailable),
               func.max(GlobalSource.last_synced_at))
        .where(GlobalSource.id.in_(select(Source.global_source_id).where(Source.enabled.is_(True))))
        .group_by(GlobalSource.kind))
    return {k.value: {"sources": n, "errors": e, "unavailable": u, "last_synced_at": last}
            for k, n, e, u, last in rows}


async def actions(session: AsyncSession, *, page: int = 1, per_page: int = 50) -> dict:
    stmt = (select(AdminAction, User.email).outerjoin(User, User.id == AdminAction.admin_id)
            .order_by(AdminAction.id.desc()))
    total = await count(session, stmt)
    rows = (await session.execute(page_of(stmt, page, per_page))).all()
    names = await org_names(session, [a.organization_id for a, _ in rows])
    return {"items": [action_out(a, email, names.get(a.organization_id)) for a, email in rows], "total": total,
            "page": page, "per_page": per_page}


def action_out(a: AdminAction, email: str | None, org_name: str | None = None) -> dict:
    return {"id": a.id, "at": a.at, "admin": email, "action": a.action, "organization_id": a.organization_id,
            "organization": org_name, "target_type": a.target_type, "target_id": a.target_id, "details": a.details}

