"""Форматы Контент Завода. Перенос FORMATS из VM_SM `app/factory.py` + LinkedIn, VK и статья.

Длины — в символах; min/max проверяет QA кодом (max — жёсткий предел площадки или письма)."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    min: int
    max: int
    multiline: bool = True


@dataclass(frozen=True)
class Format:
    key: str
    label: str
    fields: tuple[Field, ...]
    rules: str


FORMATS: dict[str, Format] = {f.key: f for f in (
    Format("telegram", "Пост в Telegram", (Field("text", "Текст поста", 400, 4096),),
           "600–1200 знаков (предел Telegram — 4096), короткие абзацы, 0–3 эмодзи по делу, один призыв в конце, "
           "без хештегов."),
    Format("email", "E-mail рассылка", (Field("subject", "Тема письма", 10, 60, False),
                                        Field("preheader", "Прехедер", 20, 90, False),
                                        Field("text", "Текст письма", 800, 3000)),
           "Тема до 60 знаков без кликбейта; прехедер до 90 знаков дополняет тему, а не повторяет её; письмо "
           "1200–2000 знаков: обращение, проблема читателя, разбор, вывод, одна кнопка-призыв "
           "отдельной строкой «Кнопка: текст кнопки»."),
    Format("linkedin", "Пост в LinkedIn", (Field("text", "Текст поста", 500, 3000),),
           "Первые 2 строки — крючок (видны до «ещё»); 800–1600 знаков; абзацы по 1–2 предложения; вывод и вопрос "
           "к читателю или призыв; 3–5 хештегов в конце."),
    Format("vk", "Пост во ВКонтакте", (Field("text", "Текст поста", 400, 4000),),
           "600–1500 знаков, живой язык, короткие абзацы, эмодзи умеренно, призыв с понятным действием "
           "(написать в сообщения, перейти по ссылке)."),
    Format("article", "Статья", (Field("title", "Заголовок", 20, 120, False), Field("lead", "Лид", 100, 400),
                                 Field("text", "Текст статьи (Markdown)", 3000, 20000)),
           "Заголовок до 120 знаков с пользой для читателя; лид 1–3 предложения; текст 4000–8000 знаков в Markdown: "
           "подзаголовки ##, списки, примеры, вывод и призыв в конце."),
)}


def field_keys(fmt: str) -> list[str]:
    return [f.key for f in FORMATS[fmt].fields]
