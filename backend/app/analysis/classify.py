"""Content Classifier: разметка постов по таксономии организации. Перенос из VM_SM app/llm/classify.py.

Отличия: таксономия организации вместо зашитой ниши, посты идут пачками (один вызов модели на BATCH постов —
дешевле в разы), ответ валидируется, неверные значения заменяются запасными, а не роняют пачку."""
import json

from pydantic import BaseModel

from app.ai import prompts
from app.ai.router import AIRouter, model_for
from app.analysis.taxonomy import BROAD_ROLE, FALLBACK, OTHER_TOPIC, OrgTaxonomy
from app.models import GlobalPost, GlobalSource

BATCH = 10
MIN_TEXT = 15
BOOL_FIELDS = ("has_case", "has_numbers", "has_offer", "has_lead_magnet")
NO_TEXT = "нет текста"


class BatchAnswer(BaseModel):
    items: list[dict]


def system_prompt(tax: OrgTaxonomy) -> str:
    fields = json.dumps({k: " | ".join(v) for k, v in tax.fields.items()}, ensure_ascii=False, indent=1)
    return prompts.load("classifier/system").replace("{niche}", tax.niche).replace("{fields}", fields)


def _norm_value(value) -> str:
    return " ".join(str(value or "").split()).strip().lower()


def normalize(raw: dict, tax: OrgTaxonomy) -> dict:
    out: dict = {}
    fallback = {**FALLBACK, "topic": OTHER_TOPIC, "target_role": BROAD_ROLE}
    for field, values in tax.fields.items():
        v = _norm_value(raw.get(field))
        # модель иногда пишет «захват лида» вместо «захват_лида» и наоборот
        match = next((x for x in values if x == v or x.replace("_", " ") == v.replace("_", " ")), None)
        out[field] = match or fallback[field]
    for f in BOOL_FIELDS:
        v = raw.get(f)
        out[f] = v is True or str(v).lower() in ("true", "1", "да")
    out["summary"] = str(raw.get("summary") or "")[:300]
    return out


def needs_text(post: GlobalPost) -> bool:
    return len((post.text or "").strip()) + len((post.title or "").strip()) < MIN_TEXT


def user_prompt(posts: list[tuple[GlobalPost, GlobalSource]]) -> str:
    items = [{"id": p.id, "источник": gs.title or gs.key, "площадка": gs.kind.value, "формат": p.media_type,
              "текст": "\n".join(filter(None, [p.title, (p.text or "")[:1500]]))} for p, gs in posts]
    return json.dumps(items, ensure_ascii=False)


async def classify_batch(router: AIRouter, tax: OrgTaxonomy, posts: list[tuple[GlobalPost, GlobalSource]], *,
                         org_id: int, job_id: int | None = None) -> dict[int, dict]:
    """→ {post_id: разметка}. Посты, которых нет в ответе модели, в словарь не попадают (разметятся в следующий раз)."""
    data = await router.run("classify", system_prompt(tax), user_prompt(posts), org_id=org_id,
                            operation="classify_posts", schema=BatchAnswer, job_id=job_id,
                            max_tokens=250 * len(posts) + 200)
    ids = {p.id for p, _ in posts}
    out: dict[int, dict] = {}
    for raw in data["items"]:
        try:
            pid = int(raw.get("id"))
        except (TypeError, ValueError):
            continue
        if pid in ids:
            out[pid] = normalize(raw, tax)
    return out


def classifier_model() -> str:
    return model_for("classify")
