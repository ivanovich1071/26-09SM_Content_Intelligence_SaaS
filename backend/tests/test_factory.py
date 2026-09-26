import json
import uuid

import pytest
from sqlalchemy import func, select

from app.ai.openrouter import AIError, AIResult
from app.ai.router import AIRouter
from app.factory import brand, context, export, pipeline, qa
from app.models import Competitor, ContentOpportunity, ContentProject, SourceRole, UsageEvent
from app.workers.tasks import run_job
from tests.conftest import register, set_plan
from tests.test_analysis import FakeEmbedder
from tests.test_roles import _join
from tests.test_sources import web  # noqa: F401 — фикстура «интернета»
from tests.test_topics import add_posts

COMPETITOR_TEXT = ("Кейс дистрибьютора: как мы сократили время обработки заявок с четырёх часов до двадцати минут "
                   "с помощью агента, который сам разбирает почту и заводит сделки в CRM без участия менеджера")
DRAFT = ("Почему заявки теряются? Разбираем, как ИИ-агент берёт на себя рутину отдела продаж.\n\n"
         "Мы сократили время ответа клиенту на 37% у [название клиента]. Агент читает почту, заводит сделку и "
         "напоминает менеджеру о следующем шаге. Менеджер "
         "видит в CRM всю историю переписки и не тратит утро на разбор входящих.\n\n"
         "Результат за 3 месяца: 120 новых заявок без найма.\n\n"
         "Хотите так же? Напишите нам в сообщения — покажем на ваших процессах.")


class FakeAI:
    """Writer / Editor / QA / бренд — по системному промпту."""
    name = "fake"

    def __init__(self, fail_writer: Exception | None = None, draft: str = DRAFT):
        self.fail_writer, self.draft = fail_writer, draft
        self.calls: list[tuple[str, dict, str]] = []

    async def chat_json(self, model, system, user, *, temperature, max_tokens):
        data = json.loads(user)
        role = ("writer" if "автор контента" in system else "editor" if "редактор контента" in system else
                "qa" if "выпускающий редактор" in system else "brand" if "бренд-стратег" in system else "?")
        self.calls.append((role, data, system))
        usage = {"prompt_tokens": 2000, "completion_tokens": 600, "cost": 0.004}
        if role == "qa":
            return AIResult(data={"checks": [{"code": "tone", "level": "ok", "message": ""},
                                             {"code": "facts", "level": "warn", "message": "«120 новых заявок» — нет "
                                                                                          "в фактах бренда"},
                                             {"code": "cta", "level": "bogus", "message": "уровень неизвестен"}],
                                  "summary": "почти готово"}, model=model, provider=self.name, usage=usage)
        if role == "brand":
            return AIResult(data={"description": "Внедряем ИИ-агентов", "offer": "ИИ-диагностика", "audience": "МСБ",
                                  "differentiators": ["свои агенты"], "proof_points": [], "cta": "Записаться",
                                  "tone": "Деловой, без хайпа", "do": ["короткие абзацы"], "dont": ["канцелярит"]},
                            model=model, provider=self.name, usage=usage)
        if self.fail_writer:
            raise self.fail_writer
        fields = json.loads(system.rsplit("с полями ", 1)[1].split("}", 1)[0] + "}")
        text = self.draft if role == "writer" else self.draft + f"\n\nP.S. {data['инструкция']}"
        return AIResult(data={k: (text if k == "text" else f"{k}: Как ИИ-агент разгружает отдел продаж")
                              for k in fields}, model=model, provider=self.name, usage=usage)


def handler(ai=None, embedder=None):
    async def run(session, job):
        router = AIRouter(session, ai, backoff_sec=0) if ai else None
        return await pipeline.handle_generate_content(session, job, router=router, embedder=embedder)
    return run


async def world(client, session):
    acc = await register(client, f"ИИ-агентство-{uuid.uuid4().hex[:4]}")
    await set_plan(acc.org_id, "starter")
    comp = Competitor(organization_id=acc.org_id, name="ИИ Лаб")
    session.add(comp)
    await session.commit()
    await add_posts(session, acc.org_id, role=SourceRole.competitor, topic="кейсы и результаты", n=6,
                    competitor_id=comp.id, texts=[COMPETITOR_TEXT])
    await add_posts(session, acc.org_id, role=SourceRole.own, topic="продукт и услуги", n=2,
                    texts=["Наш агент для HR проводит первичные собеседования"])
    opp = ContentOpportunity(organization_id=acc.org_id, rank=1, title="Кейс: заявки за 20 минут",
                             topic="кейсы и результаты", why="Пробел 40 п.п.", angle="Свой разбор",
                             market={"share_market": 60, "share_own": 0, "gap": 60, "trend_pp": 5}, examples=[])
    session.add(opp)
    await session.commit()
    await acc.put("/api/v1/brand", json={"company": "Нейроника", "proof_points": ["Сократили время ответа на 37%"],
                                         "cta": "Напишите нам", "tone": "Деловой", "examples": ["Образец поста"]})
    return acc, opp


async def run(acc, job_id: int, ai=None, embedder=None) -> dict:
    await run_job(job_id, handler(ai, embedder))
    return (await acc.get(f"/api/v1/jobs/{job_id}")).json()


# --- чистые функции ---

def test_code_checks():
    ctx = {"market_posts": [{"post_id": 7, "source": "ИИ Лаб", "url": "https://t.me/x/7", "text": COMPETITOR_TEXT}]}
    checks = qa.code_checks("email", {"subject": "Коротко", "preheader": "", "text": "x" * 3100}, ctx, "")
    msgs = {c["code"]: c for c in checks}
    assert {c["level"] for c in checks if c["code"] == "length"} == {"warn", "error"}
    assert "Прехедер" in next(c["message"] for c in checks if c["level"] == "error" and "пустое" in c["message"])
    assert msgs["cta"]["level"] == "warn"

    good = qa.code_checks("telegram", {"text": DRAFT}, ctx, "Сократили время ответа на 37%")
    codes = {c["code"]: c for c in good}
    assert "cta" not in codes and "length" not in codes
    assert codes["placeholders"]["items"] == ["[название клиента]"]
    assert codes["facts"]["items"] == ["120"]  # 37% есть в фактах бренда, 3 — не «цифра факта»

    copied = qa.code_checks("telegram", {"text": COMPETITOR_TEXT + " Напишите нам."}, ctx, "")
    sim = next(c for c in copied if c["code"] == "similarity")
    assert sim["level"] == "error" and sim["post_id"] == 7

    assert qa.merge([], None) == {"status": "ok", "checks": [], "ai": False}
    assert qa.merge([{"level": "warn"}], [{"level": "error", "code": "x", "message": "m"}])["status"] == "error"


def test_export():
    md = export.markdown("email", {"subject": "Тема", "preheader": "Пре", "text": "Текст"})
    assert md.startswith("**Тема:** Тема\n**Прехедер:** Пре")
    html = export.to_html("email", {"subject": "Тема <x>", "preheader": "Пре", "text":
                                    "Привет!\nВторая строка\n\nКнопка: Записаться\n\n<script>alert(1)</script>"})
    assert "<title>Тема &lt;x&gt;</title>" in html and "display:none" in html and ">Записаться</a>" in html
    assert "<script>" not in html and "Привет!<br>Вторая строка" in html
    art = export.to_html("article", {"title": "Заголовок", "lead": "Лид", "text": "## Раздел\n- один\n- **два**"})
    assert "<h1>Заголовок</h1>" in art and "<h3>Раздел</h3>" in art and "<li><b>два</b></li>" in art
    assert export.markdown("article", {"title": "З", "lead": "Л", "text": "Т"}) == "# З\n\n_Л_\n\nТ\n"


# --- бренд ---

async def test_brand_get_put_and_roles(client):
    acc = await register(client)
    await set_plan(acc.org_id, "starter")
    empty = (await acc.get("/api/v1/brand")).json()
    assert empty["proof_points"] == [] and empty["tone"] is None and empty["updated_at"] is None
    r = await acc.put("/api/v1/brand", json={"tone": "  Дружелюбный  ", "do": ["  коротко ", "", "с цифрами"],
                                             "examples": ["Пример\nпоста"]})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["tone"] == "Дружелюбный" and b["do"] == ["коротко", "с цифрами"] and b["examples"] == ["Пример\nпоста"]
    assert b["source"] == "manual"
    viewer = await _join(client, acc, "viewer")
    assert (await viewer.put("/api/v1/brand", json={})).status_code == 403
    assert (await viewer.get("/api/v1/brand")).json()["tone"] == "Дружелюбный"
    assert (await acc.post("/api/v1/brand/suggest", json={})).status_code == 503  # ключа нет


async def test_brand_suggest_from_site(client, session, web):  # noqa: F811
    acc = await register(client, "Нейроника")
    ai = FakeAI()
    data = await brand.suggest(session, acc.org_id, "example.com", AIRouter(session, ai, backoff_sec=0))
    assert data["tone"] == "Деловой, без хайпа" and data["website"] == "https://example.com/"
    assert data["company"] == "Нейроника"
    sent = ai.calls[0][1]
    assert sent["сайт"]["title"] == "Компания" and sent["свои_публикации"] == []
    other = await register(client)
    with pytest.raises(ValueError):
        await brand.suggest(session, other.org_id, None, AIRouter(session, ai, backoff_sec=0))


# --- Контент Завод ---

async def test_write_edit_manual_qa_and_export(client, session):
    acc, opp = await world(client, session)
    r = await acc.post("/api/v1/factory/projects", json={"title": "Как ИИ-агент разгружает отдел продаж",
                                                         "brief": "Для собственников", "format": "telegram",
                                                         "opportunity_id": opp.id})
    assert r.status_code == 201, r.text
    project = r.json()
    assert project["job"]["status"] == "queued" and project["items"] == []
    await session.refresh(opp)
    assert opp.status == "in_factory"

    ai = FakeAI()
    job = await run(acc, project["job"]["id"], ai, FakeEmbedder())
    assert job["status"] == "completed", job
    p = (await acc.get(f"/api/v1/factory/projects/{project['id']}")).json()
    v1 = p["items"][0]
    assert v1["number"] == 1 and v1["kind"] == "write" and v1["fields"]["text"] == DRAFT
    ctx = v1["context"]
    assert ctx["search"] == "semantic" and ctx["topic"] == "кейсы и результаты"
    assert all(x["competitor"] for x in ctx["market_posts"]) and ctx["own_posts"]
    assert ctx["opportunity"]["почему"] == "Пробел 40 п.п." and ctx["patterns"]["публикаций_рынка"] == 6

    role, sent, system = ai.calls[0]
    assert role == "writer" and "Сократили время ответа на 37%" in system and "Пост в Telegram" in system
    assert sent["контекст"]["лучшие_публикации_рынка"][0]["кто"] == "ИИ Лаб"
    assert sent["образцы_голоса_бренда"] == ["Образец поста"]
    checks = v1["qa"]["checks"]
    assert v1["qa"]["ai"] and v1["qa"]["status"] == "warn"
    assert {c["source"] for c in checks} == {"code", "ai"}
    assert next(c for c in checks if c["code"] == "cta" and c["source"] == "ai")["level"] == "warn"  # bogus → warn
    assert p["qa"] == "warn" and p["versions"] == 1

    assert (await acc.post(f"/api/v1/factory/projects/{p['id']}/generate", json={"action": "edit"})
            ).status_code == 422
    r = await acc.post(f"/api/v1/factory/projects/{p['id']}/generate",
                       json={"action": "edit", "instruction": "Добавь вопрос к читателю"})
    assert r.status_code == 202
    await run(acc, r.json()["id"], ai, FakeEmbedder())
    role, sent, _ = ai.calls[-2]  # последний — QA
    assert role == "editor" and sent["инструкция"] == "Добавь вопрос к читателю"
    assert any("120" in m for m in sent["замечания_проверки"])
    p = (await acc.get(f"/api/v1/factory/projects/{p['id']}")).json()
    assert [v["number"] for v in p["items"]] == [2, 1] and p["items"][0]["kind"] == "edit"
    assert p["items"][0]["context"] == ctx  # правка — на том же контексте

    r = await acc.post(f"/api/v1/factory/projects/{p['id']}/versions",
                       json={"fields": {"text": COMPETITOR_TEXT + "\n\nНапишите нам.", "лишнее": "x"}})
    assert r.status_code == 201
    v3 = r.json()
    assert v3["kind"] == "manual" and list(v3["fields"]) == ["text"] and not v3["qa"]["ai"]
    assert v3["qa"]["status"] == "error" and any(c["code"] == "similarity" for c in v3["qa"]["checks"])

    r = await acc.post(f"/api/v1/factory/projects/{p['id']}/generate", json={"action": "qa"})
    await run(acc, r.json()["id"], ai, FakeEmbedder())
    p = (await acc.get(f"/api/v1/factory/projects/{p['id']}")).json()
    assert p["versions"] == 3 and p["items"][0]["qa"]["ai"]

    gens = (await session.execute(select(func.count()).select_from(UsageEvent).where(
        UsageEvent.organization_id == acc.org_id, UsageEvent.metric == "generations"))).scalar_one()
    assert gens == 2  # черновик и правка; ручная версия и QA — не генерации

    md = await acc.get(f"/api/v1/factory/projects/{p['id']}/export?type=md&version=1")
    assert md.status_code == 200 and md.text == DRAFT + "\n"
    assert 'filename="content-' in md.headers["content-disposition"] and "v1.md" in md.headers["content-disposition"]
    html = await acc.get(f"/api/v1/factory/projects/{p['id']}/export?type=html")
    assert html.headers["content-type"].startswith("text/html") and "v3.html" in html.headers["content-disposition"]
    assert (await acc.get(f"/api/v1/factory/projects/{p['id']}/export?version=9")).status_code == 404

    r = await acc.patch(f"/api/v1/factory/projects/{p['id']}", json={"status": "published"})
    assert r.json()["status"] == "published"
    await session.refresh(opp)
    assert opp.status == "done"
    assert [x["id"] for x in (await acc.get("/api/v1/factory/projects?status=published")).json()] == [p["id"]]


async def test_email_and_article_fields(client, session):
    acc, _ = await world(client, session)
    for fmt, keys in (("email", ["subject", "preheader", "text"]), ("article", ["title", "lead", "text"])):
        p = (await acc.post("/api/v1/factory/projects", json={"title": f"Тема для {fmt}", "format": fmt})).json()
        await run(acc, p["job"]["id"], FakeAI(), FakeEmbedder())
        v = (await acc.get(f"/api/v1/factory/projects/{p['id']}")).json()["items"][0]
        assert list(v["fields"]) == keys
    formats = (await acc.get("/api/v1/factory/formats")).json()
    assert [f["key"] for f in formats] == ["telegram", "email", "linkedin", "vk", "article"]
    assert formats[1]["fields"][0] == {"key": "subject", "label": "Тема письма", "min": 10, "max": 60,
                                       "multiline": False}


async def test_context_fallbacks_without_embeddings(client, session):
    from app.ai.embeddings import EmbeddingError

    acc, opp = await world(client, session)

    class Broken:
        name, model, dim = "broken", "x", 1

        async def embed(self, texts):
            raise EmbeddingError("нет эмбеддингов")

    with_topic = ContentProject(organization_id=acc.org_id, title="Любая", format="telegram", opportunity_id=opp.id)
    by_words = ContentProject(organization_id=acc.org_id, title="Обработки заявок дистрибьютора", format="telegram")
    nothing = ContentProject(organization_id=acc.org_id, title="Про котиков", format="telegram")
    session.add_all([with_topic, by_words, nothing])
    await session.commit()
    ctx = await context.build(session, with_topic, Broken())
    assert ctx["search"] == "topic" and len(ctx["market_posts"]) == 5
    ctx = await context.build(session, by_words, None)
    assert ctx["search"] == "text" and ctx["market_posts"] and ctx["topic"] == "кейсы и результаты"
    ctx = await context.build(session, nothing, None)
    assert ctx["search"] is None and ctx["market_posts"] == [] and ctx["patterns"] == {}


async def test_without_key_writer_fails_but_manual_works(client, session):
    acc, _ = await world(client, session)
    p = (await acc.post("/api/v1/factory/projects", json={"title": "Тема без модели", "format": "vk"})).json()
    job = await run(acc, p["job"]["id"])
    assert job["status"] == "failed" and "OPENROUTER_API_KEY" in job["error"]
    assert (await acc.post(f"/api/v1/factory/projects/{p['id']}/generate", json={"action": "qa"})).status_code == 422
    r = await acc.post(f"/api/v1/factory/projects/{p['id']}/versions", json={"fields": {"text": DRAFT}})
    assert r.status_code == 201
    r = await acc.post(f"/api/v1/factory/projects/{p['id']}/generate", json={"action": "qa"})
    job = await run(acc, r.json()["id"])
    assert job["status"] == "completed" and job["result"]["version"] == 1

    p2 = (await acc.post("/api/v1/factory/projects", json={"title": "Тема с ошибкой модели", "format": "vk"})).json()
    job = await run(acc, p2["job"]["id"], FakeAI(fail_writer=AIError("HTTP 500", retryable=False)), FakeEmbedder())
    assert job["status"] == "failed"
    assert (await acc.get(f"/api/v1/factory/projects/{p2['id']}")).json()["versions"] == 0


async def test_quota_conflict_roles_and_isolation(client, session):
    acc, opp = await world(client, session)
    other = await register(client)
    p = (await acc.post("/api/v1/factory/projects", json={"title": "Первая тема", "format": "telegram"})).json()
    r = await acc.post(f"/api/v1/factory/projects/{p['id']}/generate", json={"action": "write"})
    assert r.status_code == 409  # первая генерация ещё в очереди

    viewer = await _join(client, acc, "viewer")
    await set_plan(acc.org_id, "free")  # 3 генерации в месяц
    from app.billing import usage
    for _ in range(3):
        await usage.record(session, acc.org_id, "generations", "content_write")
    r = await acc.post("/api/v1/factory/projects", json={"title": "Сверх лимита", "format": "telegram"})
    assert r.status_code == 402 and r.json()["detail"]["metric"] == "generations_month"
    r = await acc.post("/api/v1/factory/projects", json={"title": "Руками", "format": "telegram", "generate": False})
    assert r.status_code == 201 and r.json()["job"] is None

    assert (await other.get(f"/api/v1/factory/projects/{p['id']}")).status_code == 404
    assert (await other.patch(f"/api/v1/factory/projects/{p['id']}", json={"status": "archived"})).status_code == 404
    assert (await other.post(f"/api/v1/factory/projects/{p['id']}/versions", json={"fields": {}})).status_code == 404
    assert (await other.get(f"/api/v1/factory/projects/{p['id']}/export")).status_code == 404
    assert (await other.delete(f"/api/v1/factory/projects/{p['id']}")).status_code == 404
    assert (await other.post("/api/v1/factory/projects", json={"title": "Чужая тема", "format": "vk",
                                                               "opportunity_id": opp.id, "generate": False})
            ).status_code == 404
    assert (await other.get("/api/v1/factory/projects")).json() == []
    assert (await other.get("/api/v1/brand")).json()["proof_points"] == []

    assert (await viewer.post("/api/v1/factory/projects", json={"title": "Нельзя", "format": "vk"})).status_code == 403
    assert len((await viewer.get("/api/v1/factory/projects")).json()) == 2

    await acc.patch(f"/api/v1/factory/projects/{p['id']}", json={"status": "archived"})
    assert len((await acc.get("/api/v1/factory/projects")).json()) == 1
    assert (await acc.delete(f"/api/v1/factory/projects/{p['id']}")).status_code == 204
