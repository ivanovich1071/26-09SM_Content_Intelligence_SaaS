import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.ai.openrouter import AIResult
from app.ai.router import AIRouter
from app.billing import usage
from app.core.config import settings
from app.models import Competitor, LLMRequest, SourceRole
from tests.conftest import register, set_plan
from tests.test_analysis import FakeClassifier, FakeEmbedder, analyze, make_source
from tests.test_roles import _join

NOW = datetime.now(UTC)


class FakeInsight:
    name = "fake"

    def __init__(self):
        self.calls = 0

    async def chat_json(self, model, system, user, *, temperature, max_tokens):
        self.calls += 1
        return AIResult(data={"summary": f"разбор №{self.calls}", "hook": "цифра в первой строке",
                              "why_it_worked": "overperformance 3.0 — втрое выше медианы",
                              "patterns_to_use": ["начинать с цифры"], "do_not_copy": ["их бренд"]},
                        model=model, provider="fake", usage={"cost": 0.003})


@pytest.fixture
def insight_ai(monkeypatch):
    from app.posts import insight
    fake = FakeInsight()
    monkeypatch.setattr(insight, "AIRouter", lambda session: AIRouter(session, fake, backoff_sec=0))
    monkeypatch.setattr(settings, "openrouter_api_key", "key")
    return fake


@pytest.fixture
def query_embedder(monkeypatch):
    from app.posts import router
    emb = FakeEmbedder()
    monkeypatch.setattr(router, "get_embedding_provider", lambda: emb)
    monkeypatch.setattr(settings, "openrouter_api_key", "key")
    return emb


async def world(client, session):
    """Своя площадка, конкурент и рынок: 12 постов с разными метриками, датами и темами."""
    acc = await register(client, f"Org-{uuid.uuid4().hex[:4]}")
    await set_plan(acc.org_id, "starter")
    own = await make_source(session, acc.org_id, [
        {"text": "Как мы обжариваем зерно: процесс и цифры", "views": 100, "likes": 10},
        {"text": "Новый сезонный латте с тыквой", "views": 200, "likes": 2},
        {"text": "Скидка 50% по средам — приходите", "views": 300, "likes": 30},
        {"text": "Команда бариста на чемпионате", "views": 400, "likes": 4},
    ])
    comp = Competitor(organization_id=acc.org_id, name="Кофе Лаб")
    session.add(comp)
    await session.commit()
    rival = await make_source(session, acc.org_id, [
        {"text": f"Конкурент пост {i} про эспрессо", "views": 100 * (i + 1), "likes": i,
         "published_at": NOW - timedelta(days=i * 10)} for i in range(5)])
    market = await make_source(session, acc.org_id, [
        {"text": "Рынок кофе растёт на 12% в год", "views": 50}, {"text": "Отчёт по кофейням Минска", "views": 60},
        {"text": "Тренды 2026: альтернатива молоку", "views": 5000}])
    own.role = SourceRole.own
    rival.role, rival.competitor_id = SourceRole.competitor, comp.id
    await session.commit()
    await analyze(acc, session, own.id, provider=FakeClassifier(topic="продукт и услуги"))
    await analyze(acc, session, rival.id, provider=FakeClassifier(topic="экспертиза и советы"))
    await analyze(acc, session, market.id, provider=FakeClassifier(topic="новости отрасли"))
    return acc, own, rival, market, comp


async def all_pages(acc, url: str, limit: int = 2) -> list[dict]:
    items, cursor, seen = [], None, 0
    while True:
        sep = "&" if "?" in url else "?"
        page = (await acc.get(f"{url}{sep}limit={limit}" + (f"&cursor={cursor}" if cursor else ""))).json()
        items += page["items"]
        cursor = page["next_cursor"]
        seen += 1
        assert seen < 50
        if not cursor:
            return items


async def test_feed_filters(client, session):
    acc, own, rival, market, comp = await world(client, session)
    everything = (await acc.get("/api/v1/posts?limit=100")).json()["items"]
    assert len(everything) == 12
    p = everything[0]
    assert p["source"]["name"] and p["analysis"]["topic"] and p["has_embedding"]

    def ids(r):
        return {x["id"] for x in r.json()["items"]}

    own_ids = ids(await acc.get("/api/v1/posts?role=own&limit=100"))
    assert len(own_ids) == 4
    by_comp = (await acc.get(f"/api/v1/posts?competitor_id={comp.id}&limit=100")).json()["items"]
    assert len(by_comp) == 5 and all(x["source"]["competitor_name"] == "Кофе Лаб" for x in by_comp)
    assert len(ids(await acc.get("/api/v1/posts?topic=новости отрасли&topic=продукт и услуги&limit=100"))) == 7
    assert len(ids(await acc.get(f"/api/v1/posts?source_id={market.id}&limit=100"))) == 3
    top = (await acc.get("/api/v1/posts?min_overperformance=1.5&limit=100")).json()["items"]
    assert top and all(x["overperformance"] >= 1.5 for x in top)
    recent = (await acc.get(f"/api/v1/posts?competitor_id={comp.id}&date_from={(NOW - timedelta(days=15)).isoformat()}"
                            .replace("+", "%2B"))).json()["items"]
    assert len(recent) == 2
    assert ids(await acc.get("/api/v1/posts?has_cta=true")) == set()  # фейковый классификатор ставит «нет»
    assert len(ids(await acc.get("/api/v1/posts?funnel_stage=захват_лида&limit=100"))) == 12
    assert (await acc.get("/api/v1/posts?role=nobody")).status_code == 422


@pytest.mark.parametrize("sort", ["recent", "top", "er"])
async def test_cursor_pagination_is_complete_and_ordered(client, session, sort):
    acc, *_ = await world(client, session)
    full = (await acc.get(f"/api/v1/posts?sort={sort}&limit=100")).json()["items"]
    paged = await all_pages(acc, f"/api/v1/posts?sort={sort}", limit=3)
    assert [p["id"] for p in paged] == [p["id"] for p in full] and len(full) == 12
    if sort == "top":
        values = [p["overperformance"] if p["overperformance"] is not None else -1 for p in paged]
        assert values == sorted(values, reverse=True)
    assert (await acc.get("/api/v1/posts?cursor=@@@")).status_code == 422


async def test_text_search_and_escaping(client, session, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    acc, *_ = await world(client, session)
    r = (await acc.get("/api/v1/posts?q=эспрессо")).json()
    assert r["search_mode"] == "text" and len(r["items"]) == 5
    assert len((await acc.get("/api/v1/posts?q=50%")).json()["items"]) == 1  # % — буква, а не шаблон
    assert (await acc.get("/api/v1/posts?q=нет такого")).json()["items"] == []


async def test_semantic_search_ranks_by_meaning(client, session, query_embedder):
    acc, *_ = await world(client, session)
    # FakeEmbedder детерминирован: запрос с тем же текстом, что и пост, даёт тот же вектор
    r = (await acc.get("/api/v1/posts?q=Скидка 50% по средам — приходите&sort=relevance&limit=3")).json()
    assert r["search_mode"] == "semantic" and r["items"][0]["text"] == "Скидка 50% по средам — приходите"
    paged = await all_pages(acc, "/api/v1/posts?q=кофе&sort=relevance", limit=5)
    assert len(paged) == 12 and len({p["id"] for p in paged}) == 12
    logged = (await session.execute(select(func.count()).select_from(LLMRequest).where(
        LLMRequest.organization_id == acc.org_id, LLMRequest.operation == "search_query"))).scalar_one()
    assert logged >= 2


async def test_semantic_falls_back_to_text(client, session, query_embedder):
    acc, *_ = await world(client, session)
    query_embedder.fail = True
    r = (await acc.get("/api/v1/posts?q=эспрессо&search=semantic")).json()
    assert r["search_mode"] == "text" and "по словам" in r["notice"] and len(r["items"]) == 5


async def test_duplicates_hidden_by_default(client, session):
    acc = await register(client)
    text = "Одна и та же длинная новость про открытие новой кофейни в центре города и акции недели. " * 2
    first = await make_source(session, acc.org_id, [{"text": text}])
    await make_source(session, acc.org_id, [{"text": text}])
    second_src = (await acc.get("/api/v1/sources")).json()[-1]
    await analyze(acc, session, second_src["id"])
    assert len((await acc.get("/api/v1/posts")).json()["items"]) == 1
    both = (await acc.get("/api/v1/posts?include_duplicates=true")).json()["items"]
    assert len(both) == 2 and any(p["duplicate_of_id"] for p in both)
    _ = first


async def test_post_detail_similar_and_isolation(client, session):
    acc, own, *_ = await world(client, session)
    post = (await acc.get(f"/api/v1/posts?source_id={own.id}&sort=top")).json()["items"][0]
    d = (await acc.get(f"/api/v1/posts/{post['id']}")).json()
    assert d["post"]["id"] == post["id"] and d["source_median_views"] == 250
    assert len(d["similar"]) == 5 and post["id"] not in [s["id"] for s in d["similar"]]
    assert d["insight"] is None

    other = await register(client)
    assert (await other.get(f"/api/v1/posts/{post['id']}")).status_code == 404
    assert (await other.post(f"/api/v1/posts/{post['id']}/analyze")).status_code == 404
    assert (await other.get("/api/v1/posts")).json()["items"] == []


async def test_ai_breakdown_cached_forced_and_per_org(client, session, insight_ai):
    acc, own, *_ = await world(client, session)
    post = (await acc.get(f"/api/v1/posts?source_id={own.id}")).json()["items"][0]
    r = await acc.post(f"/api/v1/posts/{post['id']}/analyze")
    assert r.status_code == 200 and r.json()["insight"]["summary"] == "разбор №1"
    assert r.json()["insight"]["pain_point"] == ""  # не вернула модель — пустое, а не ошибка
    again = await acc.post(f"/api/v1/posts/{post['id']}/analyze")
    assert again.json()["insight"]["summary"] == "разбор №1" and insight_ai.calls == 1  # из кэша
    forced = await acc.post(f"/api/v1/posts/{post['id']}/analyze?force=true")
    assert forced.json()["insight"]["summary"] == "разбор №2"
    assert (await acc.get(f"/api/v1/posts/{post['id']}")).json()["insight"]["summary"] == "разбор №2"

    # второй клиент с тем же каналом не видит чужой разбор
    from app.models import Source
    b = await register(client, "B")
    session.add(Source(organization_id=b.org_id, global_source_id=own.global_source_id))
    await session.commit()
    assert (await b.get(f"/api/v1/posts/{post['id']}")).json()["insight"] is None


async def test_breakdown_roles_key_and_quota(client, session, insight_ai, monkeypatch):
    acc, own, *_ = await world(client, session)
    post = (await acc.get(f"/api/v1/posts?source_id={own.id}")).json()["items"][0]
    viewer = await _join(client, acc, "viewer")
    assert (await viewer.get(f"/api/v1/posts/{post['id']}")).status_code == 200
    assert (await viewer.post(f"/api/v1/posts/{post['id']}/analyze")).status_code == 403

    await usage.record(session, acc.org_id, "ai_cost_usd", "test", 100)
    assert (await acc.post(f"/api/v1/posts/{post['id']}/analyze")).status_code == 402
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    assert (await acc.post(f"/api/v1/posts/{post['id']}/analyze")).status_code == 503



async def test_duplicate_hidden_only_when_original_is_visible_to_org(client, session):
    """Регрессия: пост-копия не должен пропадать у клиента, который подписан только на канал с копией."""
    from app.models import Source
    text = "Большой разбор: как кофейня увеличила выручку на 30% за квартал с помощью программы лояльности. " * 2
    a = await register(client, "A")
    original = await make_source(session, a.org_id, [{"text": text}])
    await analyze(a, session, original.id)

    b = await register(client, "B")
    copy = await make_source(session, b.org_id, [{"text": text, "published_at": NOW + timedelta(hours=1)}])
    rb = await analyze(b, session, copy.id)
    assert rb["duplicates"] == 1  # глобально это дубль …
    items = (await b.get("/api/v1/posts")).json()["items"]
    assert len(items) == 1 and items[0]["duplicate_of_id"] is not None  # … но у B он единственный — виден
    assert items[0]["analysis"] and not items[0]["analysis"]["error"]  # и размечен для B

    session.add(Source(organization_id=a.org_id, global_source_id=copy.global_source_id))
    await session.commit()
    assert len((await a.get("/api/v1/posts")).json()["items"]) == 1  # A видит оригинал, копия скрыта
