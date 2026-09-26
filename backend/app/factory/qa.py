"""QA кодом: длины по формату, незаполненные шаблоны, призыв к действию, цифры вне контекста и близость
к публикациям рынка (общие цепочки из 5 слов). Модель добавляет проверки фактов, тона и структуры."""
import re

from app.factory.formats import FORMATS

SHINGLE = 5
NEAR_ERROR, NEAR_WARN = 0.25, 0.12  # доля 5-словных цепочек черновика, совпавших с постом рынка
PLACEHOLDER_RE = re.compile(r"\[[^\[\]\n]{2,120}\]")
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?\s?%|\b\d{2,}(?:[ .,]\d{3})*\b")
CTA_RE = re.compile(r"https?://|t\.me/|@\w{4,}|кнопка:|подпис|напиш|запиш|оставь|остав(ьте|ляйте)|переход|перейд|"
                    r"скача|зарегистр|получит|закаж|звон|обращай|приход|жд[её]м|в комментари|ссылк|директ|сообщени",
                    re.I)
LEVELS = {"ok": 0, "warn": 1, "error": 2}


def _check(code: str, level: str, message: str, **extra) -> dict:
    return {"code": code, "level": level, "message": message, "source": "code", **extra}


def _shingles(text: str) -> set[tuple[str, ...]]:
    words = re.findall(r"\w+", text.lower())
    return {tuple(words[i:i + SHINGLE]) for i in range(len(words) - SHINGLE + 1)}


def _numbers(text: str) -> set[str]:
    return {re.sub(r"\s", "", m).replace(",", ".") for m in NUMBER_RE.findall(text)}


def code_checks(fmt: str, fields: dict, context: dict, allowed_text: str) -> list[dict]:
    """allowed_text — всё, откуда можно брать факты: профиль бренда, тема и пожелания, цифры «Стратегии»."""
    checks: list[dict] = []
    spec = FORMATS[fmt]
    for f in spec.fields:
        n = len((fields.get(f.key) or "").strip())
        if n == 0:
            checks.append(_check("length", "error", f"«{f.label}» пустое"))
        elif n > f.max:
            checks.append(_check("length", "error", f"«{f.label}»: {n} знаков — больше предела {f.max}", field=f.key))
        elif n < f.min:
            checks.append(_check("length", "warn", f"«{f.label}»: {n} знаков — короче рекомендуемых {f.min}",
                                 field=f.key))
    body = "\n".join(str(fields.get(f.key) or "") for f in spec.fields)

    holes = sorted(set(PLACEHOLDER_RE.findall(body)))
    if holes:
        checks.append(_check("placeholders", "warn", f"Заполните шаблоны перед публикацией: {', '.join(holes[:6])}",
                             items=holes))

    text = fields.get("text") or ""
    if not CTA_RE.search(text[int(len(text) * 0.6):]):
        checks.append(_check("cta", "warn", "В конце нет явного призыва к действию"))

    allowed = _numbers(allowed_text)
    extra = sorted(n for n in _numbers(PLACEHOLDER_RE.sub("", body)) if n not in allowed)
    if extra:
        checks.append(_check("facts", "warn", "Цифры не из профиля бренда и контекста — проверьте или замените "
                                              f"шаблоном [что подставить]: {', '.join(extra[:8])}", items=extra))

    mine = _shingles(body)
    worst, worst_post = 0.0, None
    for p in context.get("market_posts", []):
        if mine:
            share = len(mine & _shingles(p.get("text") or "")) / len(mine)
            if share > worst:
                worst, worst_post = share, p
    if worst_post and worst >= NEAR_WARN:
        level = "error" if worst >= NEAR_ERROR else "warn"
        checks.append(_check("similarity", level, f"Текст близок к публикации «{worst_post['source']}»: "
                                                  f"{round(100 * worst)}% совпадающих фраз — перепишите своими словами",
                             post_id=worst_post["post_id"], url=worst_post.get("url")))
    return checks


def merge(code: list[dict], ai: list[dict] | None) -> dict:
    checks = code + [{**c, "source": "ai"} for c in ai or []]
    status = max((c["level"] for c in checks), key=LEVELS.get, default="ok")
    return {"status": status, "checks": checks, "ai": ai is not None}
