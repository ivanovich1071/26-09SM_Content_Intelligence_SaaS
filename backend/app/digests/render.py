"""Тексты дайджеста: шаблон по цифрам (без модели) и сборка Markdown / HTML-письма из цифр + текстов."""
import html

from app.factory.export import body_html

FORMATS = ("telegram", "email", "linkedin", "vk", "article")
SECTION_TITLES = [("market", "Рынок"), ("topics", "Темы"), ("competitors", "Конкуренты"),
                  ("top_posts", "Лучшие публикации"), ("unusual", "Необычные ходы"), ("own", "Ваш контент")]


def _pct(v) -> str:
    return "—" if v is None else f"{v:g}%"


def _delta(d) -> str:
    return "" if d is None else f" ({'+' if d > 0 else ''}{d:g}% к прошлому периоду)"


def fallback(st: dict) -> dict:
    """Тексты разделов из цифр — когда модель недоступна."""
    m, t, own = st["market"], st["topics"], st["own"]
    comps = st["competitors"]
    top = st["top_posts"][:1]
    sections = {
        "headline": f"Рынок: {m['posts']} публикаций за {st['period']['days']} дн.{_delta(m['delta_pct'])}",
        "summary": f"Рынок опубликовал {m['posts']} материалов{_delta(m['delta_pct'])}, медианная вовлечённость "
                   f"{_pct(m['median_er'])}. У вас — {own['posts']} публикаций{_delta(own['delta_pct'])}.",
        "market": f"{m['posts']} публикаций из {m['sources']} источников, медиана ER {_pct(m['median_er'])} "
                  f"(было {_pct(m['prev_median_er'])}).",
        "topics": "; ".join(filter(None, [
            "Растут: " + ", ".join(f"{x['topic']} (+{x['trend_pp']:g} п.п.)" for x in t["rising"])
            if t["rising"] else "",
            "Новые: " + ", ".join(x["topic"] for x in t["new"]) if t["new"] else "",
            "Пробелы: " + ", ".join(f"{x['topic']} (+{x['gap']:g} п.п.)" for x in t["gaps"]) if t["gaps"] else "",
        ])) or "Заметных изменений в темах нет.",
        "competitors": "; ".join(f"{c['name']}: {c['posts']} публикаций (было {c['prev_posts']})"
                                 + (f", изменений сайта: {len(c['site_changes'])}" if c["site_changes"] else "")
                                 for c in comps[:5]) or "Активности конкурентов за период нет.",
        "top_posts": (f"Лучший пост — {top[0]['source']}, ×{top[0]['overperformance']:g} к обычному." if top
                      else "Нет публикаций с метриками."),
        "unusual": "; ".join(f"{c['name']} впервые использует формат: {', '.join(c['new_formats'])}"
                             for c in comps if c["new_formats"]) or
                   (f"Выстрелили {len(st['outliers'])} публикаций (в 2+ раза лучше обычного)." if st["outliers"]
                    else "Необычных ходов не замечено."),
        "own": f"{own['posts']} публикаций{_delta(own['delta_pct'])}, медиана ER {_pct(own['median_er'])}."
               + (f" Балл аудита: {own['audit']['score']:g}." if own["audit"] and own["audit"]["score"] is not None
                  else ""),
        "recommendations": [{"title": f"Закрыть пробел: {g['topic']}",
                             "why": f"Рынок пишет об этом {g['share_market']:g}% времени, вы — {g['share_own']:g}%."}
                            for g in t["gaps"][:3]],
        "ideas": [{"title": o["title"], "why": o["why"], "format": (o["formats"] or ["telegram"])[0]}
                  for o in st["opportunities"]],
    }
    return sections


def clean(data: dict, st: dict) -> dict:
    """Ответ модели → разделы; пустые разделы заполняются шаблоном по цифрам."""
    base = fallback(st)
    out = {k: (str(data.get(k) or "").strip()[:3000] or base[k]) for k in base if k not in ("recommendations", "ideas")}
    out["recommendations"] = [{"title": str(r.get("title"))[:200], "why": str(r.get("why") or "")[:600]}
                              for r in data.get("recommendations") or [] if isinstance(r, dict) and r.get("title")][:5]
    out["ideas"] = [{"title": str(i.get("title"))[:200], "why": str(i.get("why") or "")[:600],
                     "format": i.get("format") if i.get("format") in FORMATS else "telegram"}
                    for i in data.get("ideas") or [] if isinstance(i, dict) and i.get("title")][:5]
    out["recommendations"] = out["recommendations"] or base["recommendations"]
    out["ideas"] = out["ideas"] or base["ideas"]
    return out


def markdown(title: str, st: dict, s: dict) -> str:
    p = st["period"]
    lines = [f"# {title}", "", f"_{p['from'][:10]} — {p['to'][:10]}_", "", f"**{s['headline']}**", "", s["summary"], ""]
    for key, name in SECTION_TITLES:
        lines += [f"## {name}", "", s[key], ""]
        if key == "top_posts":
            lines += [f"- {x['source']}: ×{x['overperformance']:g} — {x['text'][:140].replace(chr(10), ' ')}"
                      + (f" ({x['url']})" if x["url"] else "") for x in st["top_posts"]] + [""]
        if key == "competitors":
            for comp in st["competitors"]:
                lines += [f"- **{ch['site']}**: {ch['summary']}" for ch in comp["site_changes"]]
            lines += [""]
    if s["recommendations"]:
        lines += ["## Рекомендации", ""] + [f"- **{r['title']}** — {r['why']}" for r in s["recommendations"]] + [""]
    if s["ideas"]:
        lines += ["## Идеи для Контент Завода", ""] + [f"- **{i['title']}** ({i['format']}) — {i['why']}"
                                                       for i in s["ideas"]] + [""]
    return "\n".join(lines)


def email_html(title: str, st: dict, s: dict, url: str) -> str:
    body = body_html(markdown(title, st, s).split("\n", 2)[2])  # без «# заголовка» — он в шапке письма
    return ('<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            f"<title>{html.escape(title)}</title></head>"
            '<body style="margin:0;background:#f4f4f4;font-family:Arial,Helvetica,sans-serif;color:#1c1d21">'
            f'<div style="display:none;max-height:0;overflow:hidden">{html.escape(s["headline"])}</div>'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">'
            '<table role="presentation" width="640" cellpadding="24" cellspacing="0" '
            'style="background:#fff;max-width:640px;font-size:15px;line-height:1.55"><tr><td>'
            f'<h1 style="font-size:22px;margin:0 0 8px">{html.escape(title)}</h1>{body}'
            f'<p style="margin-top:24px"><a href="{html.escape(url)}" style="color:#3b5bdb">'
            "Открыть дайджест в сервисе</a></p></td></tr></table></td></tr></table></body></html>\n")
