"""RAG по базе знаний (data/knowledge_base.md).

База маленькая (один markdown-файл, ~10 разделов), поэтому поиск — лексический по
основам слов (префикс 5 букв) с весом заголовка. Интерфейс search() позволяет позже
заменить backend на Chroma/Qdrant без изменений в action.py.
База знаний общая и не содержит данных клиентов (Ст. 4), поэтому client_id не нужен;
персональные данные доступны только через tools с client_id.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from core.config import DATA_DIR

_STOP = {
    "что", "как", "это", "для", "или", "ли", "есть", "ваш", "вас", "вам", "мне", "можно", "нужно",
    "какой", "какая", "какие", "когда", "где", "при", "про", "над", "под", "без", "между",
    "подскажите", "пожалуйста", "здравствуйте", "добрый", "день", "ваши", "наш", "наши",
}
_TOKEN = re.compile(r"[\w]+", re.UNICODE)


@dataclass(frozen=True)
class KbChunk:
    title: str
    text: str
    score: float = 0.0


@lru_cache(maxsize=1)
def _chunks() -> tuple[KbChunk, ...]:
    raw = (DATA_DIR / "knowledge_base.md").read_text(encoding="utf-8")
    parts = re.split(r"^##\s+", raw, flags=re.MULTILINE)[1:]  # [0] — служебная шапка файла
    chunks = []
    for part in parts:
        title, _, body = part.partition("\n")
        chunks.append(KbChunk(title=title.strip(), text=body.strip()))
    return tuple(chunks)


def reload() -> None:
    """Сбрасывает кэш базы знаний (после замены data/knowledge_base.md)."""
    _chunks.cache_clear()


def _stems(text: str) -> set[str]:
    stems = set()
    for token in _TOKEN.findall(text.lower()):
        if len(token) < 2 or token in _STOP:
            continue
        stems.add(token[:5])
    return stems


def search(query: str, k: int = 2, min_score: float = 2.0) -> list[KbChunk]:
    """Лучшие разделы базы знаний по запросу (пусто, если ничего релевантного)."""
    query_stems = _stems(query)
    if not query_stems:
        return []
    scored = []
    for chunk in _chunks():
        title_stems, body_stems = _stems(chunk.title), _stems(chunk.text)
        score = sum(3.0 for s in query_stems if s in title_stems) + sum(
            1.0 for s in query_stems if s in body_stems
        )
        if score >= min_score:
            scored.append(KbChunk(chunk.title, chunk.text, score))
    scored.sort(key=lambda c: c.score, reverse=True)
    return scored[:k]
