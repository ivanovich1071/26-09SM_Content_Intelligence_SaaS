"""Критерии аудита, веса и оценки «по цифрам».

Общий балл считает код: взвешенное среднее по критериям с оценкой, ×10 → 0–100. Критерий без данных (null)
в балл не входит. Эвристики — ориентир для модели и запасной вариант, когда модель недоступна."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Criterion:
    key: str
    name: str
    weight: float
    what: str


CRITERIA: list[Criterion] = [
    Criterion("strategy", "Контент-стратегия", 0.20,
              "регулярность, баланс тем и стадий воронки, связность каналов и сайта"),
    Criterion("audience_fit", "Попадание в аудиторию", 0.15,
              "насколько контент адресован целевой аудитории и её болям, а не «всем»"),
    Criterion("hook", "Цепляющее начало", 0.15, "сила первых строк и заголовков: вопрос, цифра, боль, история"),
    Criterion("value", "Польза и доказательства", 0.20,
              "практическая польза, экспертиза, доказательства — кейсы, цифры, отзывы"),
    Criterion("differentiation", "Отличие от конкурентов", 0.15, "свои темы, углы и тон, а не пересказ рынка"),
    Criterion("cta", "Призыв к действию", 0.15,
              "понятный следующий шаг, лид-магниты, связка соцсети → сайт → заявка"),
]
BY_KEY = {c.key: c for c in CRITERIA}


def clamp(score) -> float | None:
    if isinstance(score, bool) or not isinstance(score, int | float):
        return None
    return round(max(0.0, min(10.0, float(score))), 1)


def overall(scores: dict[str, float | None]) -> float | None:
    pairs = [(s, BY_KEY[k].weight) for k, s in scores.items() if k in BY_KEY and s is not None]
    if not pairs:
        return None
    return round(10 * sum(s * w for s, w in pairs) / sum(w for _, w in pairs), 1)


def _n(v: float | None) -> float:
    return v or 0.0


def heuristics(own: dict, site: dict | None) -> dict[str, dict]:
    """Оценки по метрикам кода: {критерий: {score, explanation}}. Попадание в аудиторию и отличие от конкурентов
    по одним цифрам не оценить — там null."""
    out: dict[str, dict] = {c.key: {"score": None, "explanation": "Нужна оценка модели по текстам постов."}
                            for c in CRITERIA}
    posts, analyzed = own.get("posts", 0), own.get("analyzed", 0)
    forms = (site or {}).get("forms", 0)
    if not posts:
        msg = "Нет публикаций за период — оценить нельзя."
        for key in ("strategy", "hook", "value"):
            out[key] = {"score": None, "explanation": msg}
        if forms:
            out["cta"] = {"score": 3.0, "explanation": f"Публикаций нет, на сайте форм заявки: {forms}."}
        else:
            out["cta"] = {"score": None, "explanation": msg}
        return out

    ppw, idle = own.get("posts_per_week") or 0, own.get("days_since_last_post")
    stages = sum(1 for f in own.get("funnel", []) if f["share"] >= 10)
    regularity = min(10.0, ppw * 2) - (3 if idle is not None and idle > 14 else 0)
    balance = min(10.0, 2.5 * stages) if analyzed else regularity
    out["strategy"] = {"score": clamp(round(0.6 * regularity + 0.4 * balance)),
                       "explanation": f"{ppw} публикаций в неделю, последняя — {idle} дн. назад; "
                                      f"стадий воронки с долей от 10%: {stages}." if analyzed else
                                      f"{ppw} публикаций в неделю, последняя — {idle} дн. назад."}
    if not analyzed:
        return out
    hook = _n(own.get("hook_share"))
    out["hook"] = {"score": clamp(round(hook / 10)), "explanation": f"Цепляющее начало — в {hook:g}% постов."}
    case, numbers = _n(own.get("case_share")), _n(own.get("numbers_share"))
    out["value"] = {"score": clamp(round((case + numbers) / 12)),
                    "explanation": f"Кейсы — в {case:g}% постов, цифры — в {numbers:g}%."}
    cta, magnet = _n(own.get("cta_share")), _n(own.get("lead_magnet_share"))
    out["cta"] = {"score": clamp(round(cta / 6 + (3 if magnet else 0) + (2 if forms else 0))),
                  "explanation": f"Призыв к действию — в {cta:g}% постов, лид-магниты — в {magnet:g}%"
                                 + (f", на сайте форм заявки: {forms}." if site else ".")}
    return out
