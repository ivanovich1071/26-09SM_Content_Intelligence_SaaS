"""Экспорт версии: Markdown и HTML (для email — письмо с прехедером и кнопкой). Без внешних зависимостей:
разметка простая — заголовки ##, списки, **жирный**, абзацы."""
import html
import re

BUTTON_RE = re.compile(r"^\s*кнопка:\s*(.+)$", re.I)


def markdown(fmt: str, f: dict) -> str:
    if fmt == "email":
        return f"**Тема:** {f.get('subject', '')}\n**Прехедер:** {f.get('preheader', '')}\n\n{f.get('text', '')}\n"
    if fmt == "article":
        return f"# {f.get('title', '')}\n\n_{f.get('lead', '')}_\n\n{f.get('text', '')}\n"
    return f"{f.get('text', '')}\n"


def _inline(s: str) -> str:
    s = html.escape(s)
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)


def body_html(text: str) -> str:
    out, para, items = [], [], []

    def flush():
        if para:
            out.append("<p>" + "<br>".join(_inline(x) for x in para) + "</p>")
            para.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + "</ul>")
            items.clear()

    for line in text.splitlines():
        s = line.strip()
        if not s:
            flush()
        elif m := re.match(r"^(#{1,4})\s+(.*)", s):
            flush()
            level = min(len(m.group(1)) + 1, 4)
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
        elif m := re.match(r"^[-*•]\s+(.*)", s):
            if para:
                flush()
            items.append(m.group(1))
        elif m := BUTTON_RE.match(s):
            flush()
            out.append('<p style="margin:24px 0"><a href="[ссылка]" style="background:#3b5bdb;color:#fff;'
                       'padding:12px 22px;border-radius:8px;text-decoration:none;font-weight:600">'
                       f"{_inline(m.group(1))}</a></p>")
        else:
            if items:
                flush()
            para.append(s)
    flush()
    return "\n".join(out)


def to_html(fmt: str, f: dict) -> str:
    if fmt == "email":
        title, pre = html.escape(f.get("subject", "")), html.escape(f.get("preheader", ""))
        return ('<!doctype html><html lang="ru"><head><meta charset="utf-8">'
                f"<title>{title}</title></head>"
                '<body style="margin:0;background:#f4f4f4;font-family:Arial,Helvetica,sans-serif;color:#1c1d21">'
                f'<div style="display:none;max-height:0;overflow:hidden">{pre}</div>'
                '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">'
                '<table role="presentation" width="600" cellpadding="24" cellspacing="0" '
                'style="background:#fff;max-width:600px;font-size:16px;line-height:1.5"><tr><td>'
                f"{body_html(f.get('text', ''))}</td></tr></table></td></tr></table></body></html>\n")
    head = ""
    if fmt == "article":
        head = f"<h1>{_inline(f.get('title', ''))}</h1>\n<p><i>{_inline(f.get('lead', ''))}</i></p>\n"
    title = html.escape(f.get("title") or (f.get("text", "").splitlines() or [""])[0][:80])
    return (f'<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>{title}</title></head>'
            f"<body style=\"max-width:720px;margin:auto;font-family:Arial,sans-serif;line-height:1.6\">\n"
            f"{head}{body_html(f.get('text', ''))}\n</body></html>\n")
