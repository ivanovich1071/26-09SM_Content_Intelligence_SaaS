"""Промпты агентов лежат в app/ai/prompts/<агент>/*.md — не строками в коде модулей."""
from functools import cache
from pathlib import Path

DIR = Path(__file__).parent


@cache
def load(name: str) -> str:
    """load("classifier/system") → текст app/ai/prompts/classifier/system.md."""
    return (DIR / f"{name}.md").read_text(encoding="utf-8").strip()
