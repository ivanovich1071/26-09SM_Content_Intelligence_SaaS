"""Двухуровневая таксономия.

Универсальные поля одинаковы для всех ниш — по ним сравниваются любые источники. Темы и роли аудитории у каждой
организации свои: модель предлагает их при онбординге (`suggest`), пользователь правит. Пока организация их
не задала — общий набор DEFAULT_TOPICS / DEFAULT_ROLES."""
import json
from dataclasses import dataclass

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import prompts
from app.ai.router import AIRouter
from app.models import GlobalPost, GlobalSource, Organization, Source, Taxonomy

OTHER_TOPIC = "другое"
BROAD_ROLE = "широкая аудитория"

# Перенесено из VM_SM TAXONOMY без ниши «ИИ для бизнеса»; дополнено hook_type, tone, value_type
UNIVERSAL: dict[str, list[str]] = {
    "content_type": ["экспертный", "кейс", "отзыв", "продуктовый", "новость", "личный", "вакансия",
                     "развлекательный", "промо", "анонс_события", "опрос", "другое"],
    "funnel_stage": ["охват", "проблема", "доказательство", "захват_лида", "продажа", "удержание"],
    "hook_type": ["вопрос", "цифра", "боль", "провокация", "история", "новость", "список", "обещание", "нет"],
    "cta_type": ["нет", "подписка", "консультация", "покупка", "заявка", "лид_магнит", "мероприятие",
                 "личное_сообщение", "перейти_по_ссылке", "реакция_комментарий"],
    "proof_type": ["цифры", "отзыв", "демонстрация", "кейс", "экспертное_мнение", "внешняя_ссылка", "нет"],
    "tone": ["экспертный", "дружелюбный", "продающий", "вдохновляющий", "юмор", "официальный", "провокационный"],
    "value_type": ["обучение", "польза_для_работы", "новости", "выгода", "развлечение", "вдохновение",
                   "социальное_доказательство"],
}
FALLBACK = {"content_type": "другое", "funnel_stage": "охват", "hook_type": "нет", "cta_type": "нет",
            "proof_type": "нет", "tone": "дружелюбный", "value_type": "польза_для_работы"}

DEFAULT_TOPICS = ["продукт и услуги", "экспертиза и советы", "кейсы и результаты", "новости отрасли",
                  "команда и компания", "мероприятия", "акции и предложения", "отзывы клиентов"]
DEFAULT_ROLES = ["собственник бизнеса", "специалист", "руководитель отдела", "частный клиент"]


@dataclass(frozen=True)
class OrgTaxonomy:
    topics: list[str]
    roles: list[str]
    version: int
    niche: str

    @property
    def fields(self) -> dict[str, list[str]]:
        return {**UNIVERSAL, "topic": self.topics, "target_role": self.roles}


def clean_labels(values: list[str], extra: str) -> list[str]:
    """Обрезка, нижний регистр, без дублей; служебное значение (другое / широкая аудитория) — всегда последним."""
    out: list[str] = []
    for v in values:
        label = " ".join(str(v).split()).strip(" .,;").lower()[:120]
        if label and label != extra and label not in out:
            out.append(label)
    return [*out, extra]


async def get(session: AsyncSession, org_id: int) -> OrgTaxonomy:
    tax = (await session.execute(select(Taxonomy).where(Taxonomy.organization_id == org_id))).scalar_one_or_none()
    org = await session.get(Organization, org_id)
    topics = tax.topics if tax and tax.topics else DEFAULT_TOPICS
    roles = tax.roles if tax and tax.roles else DEFAULT_ROLES
    return OrgTaxonomy(topics=clean_labels(topics, OTHER_TOPIC), roles=clean_labels(roles, BROAD_ROLE),
                       version=tax.version if tax else 0,
                       niche=(tax.niche if tax and tax.niche else f"компания «{org.name}» и её конкуренты"))


async def save(session: AsyncSession, org_id: int, topics: list[str], roles: list[str], source: str,
               niche: str | None = None) -> Taxonomy:
    tax = (await session.execute(select(Taxonomy).where(Taxonomy.organization_id == org_id))).scalar_one_or_none()
    topics, roles = clean_labels(topics, OTHER_TOPIC)[:-1], clean_labels(roles, BROAD_ROLE)[:-1]
    niche = (niche or "").strip()[:500] or None
    if tax is None:
        tax = Taxonomy(organization_id=org_id, topics=topics, roles=roles, niche=niche, version=1, source=source)
        session.add(tax)
    elif (tax.topics, tax.roles, tax.niche) != (topics, roles, niche):
        tax.topics, tax.roles, tax.niche, tax.source, tax.version = topics, roles, niche, source, tax.version + 1
    await session.commit()
    return tax


class Suggestion(BaseModel):
    niche: str = ""
    topics: list[str] = Field(min_length=3, max_length=25)
    roles: list[str] = Field(min_length=2, max_length=15)
    rationale: str = ""


async def suggest(session: AsyncSession, org_id: int, router: AIRouter | None = None) -> dict:
    """Предложение тем и ролей по источникам организации и примерам постов. Не сохраняет — решает пользователь."""
    org = await session.get(Organization, org_id)
    rows = (await session.execute(
        select(Source, GlobalSource).join(GlobalSource).where(Source.organization_id == org_id))).all()
    sources = [{"роль": s.role.value, "название": s.name or gs.title or gs.key,
                "описание": (gs.description or "")[:300]} for s, gs in rows]
    posts = (await session.execute(
        select(GlobalPost.text).where(GlobalPost.global_source_id.in_([gs.id for _, gs in rows]),
                                      GlobalPost.text != "")
        .order_by(GlobalPost.published_at.desc().nulls_last()).limit(40))).scalars().all()
    user = json.dumps({"компания": org.name, "источники": sources,
                       "примеры_публикаций": [p[:400] for p in posts]}, ensure_ascii=False)
    router = router or AIRouter(session)
    data = await router.run("analyze", prompts.load("taxonomy/system"), user, org_id=org_id,
                            operation="suggest_taxonomy", schema=Suggestion, max_tokens=1500)
    data["topics"] = clean_labels(data["topics"], OTHER_TOPIC)[:-1]
    data["roles"] = clean_labels(data["roles"], BROAD_ROLE)[:-1]
    data["based_on"] = {"sources": len(sources), "posts": len(posts)}
    return data
