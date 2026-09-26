"""Сравнение снимков: значимые добавленные/удалённые строки без шума (даты, счётчики, «n минут назад»)
и эвристика категории — на случай, когда модель недоступна."""
import re

from app.connectors.website import diff_lines

NOISE = [
    re.compile(r"^[\d\s.,:/\-–—+()%]*$"),  # только цифры/даты/время
    re.compile(r"\b\d+\s*(сек|мин|час|дн|нед|мес|лет|год|sec|min|hour|day|week|month|year)\w*\s*(назад|ago)\b", re.I),
    re.compile(r"(©|copyright|все права защищены|all rights reserved)", re.I),
    re.compile(r"^(просмотр|views?|комментари|comments?|лайк|likes?)\w*[:\s]*\d+$", re.I),
]
DATES = re.compile(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}:\d{2}(:\d{2})?\b")
PRICE = re.compile(r"\d[\d\s]*([.,]\d+)?\s*(₽|руб|р\.|byn|бел|\$|usd|€|eur|тенге|₸|грн)", re.I)
CATEGORY_HINTS = [
    ("price", PRICE),
    ("contacts", re.compile(r"(\+?\d[\d\s()\-]{8,}|@[\w.]+\.\w+|адрес|телефон|e-?mail)", re.I)),
    ("offer", re.compile(r"(скидк|акци|бесплатн|подарок|промокод|только до|специальн|предложени)", re.I)),
    ("product", re.compile(r"(услуг|продукт|тариф|пакет|курс|программ|функци|возможност)", re.I)),
]
MAX_LINES = 200


def is_noise(line: str) -> bool:
    line = line.strip()
    return len(line) < 3 or any(rx.search(line) for rx in NOISE)


def meaningful_diff(old: str, new: str) -> tuple[list[str], list[str]]:
    added, removed = diff_lines(old, new)
    added = [ln for ln in added if not is_noise(ln)]
    removed = [ln for ln in removed if not is_noise(ln)]
    # строка переехала на другое место или в ней поменялась только дата/время («Обновлено 02.09») — не изменение
    def key(ln: str) -> str:
        return DATES.sub("<дата>", ln.strip())
    same = {key(ln) for ln in added} & {key(ln) for ln in removed}
    return ([ln for ln in added if key(ln) not in same][:MAX_LINES],
            [ln for ln in removed if key(ln) not in same][:MAX_LINES])


def guess(page_kind: str, added: list[str], removed: list[str]) -> tuple[str, str]:
    """→ (категория, важность) по словам и типу страницы."""
    text = "\n".join(added + removed)
    category = next((c for c, rx in CATEGORY_HINTS if rx.search(text)), "content")
    if page_kind == "pricing" or category == "price":
        return "price", "high"
    if category in ("offer", "product") or page_kind in ("service", "home"):
        return category if category != "content" else "positioning", "medium"
    return category, "low"
