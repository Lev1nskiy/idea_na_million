"""PERCEPTION: нормализация входящего сообщения и лексический pre-classifier.

pre_classify() работает без LLM и укладывается в единицы миллисекунд, поэтому
для явных споров (I) и инъекций (F) клиент получает детерминированный ответ
< 100 мс (Ст. 9). «Тонкие» споры без маркеров определит LLM на слое UNDERSTANDING.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from functools import lru_cache

from core.config import DATA_DIR
from core.guardrails import clean_text, detect_injection
from core.schemas import IncomingMessage, PreClassification

INTERIM_TIMEOUT_S = 3.5  # через столько секунд клиент получает «принял, уточняю» (Ст. 9)
MAX_TEXT_LEN = 2000


@dataclass
class Perceived:
    """Нормализованное сообщение + таймер пайплайна."""

    message: IncomingMessage
    started_at: float = field(default_factory=time.perf_counter)

    def elapsed_ms(self) -> int:
        return int((time.perf_counter() - self.started_at) * 1000)

    def remaining_s(self, budget: float = INTERIM_TIMEOUT_S) -> float:
        """Сколько секунд осталось до промежуточного ответа."""
        return max(budget - (time.perf_counter() - self.started_at), 0.0)


def normalize(msg: IncomingMessage, max_len: int = MAX_TEXT_LEN) -> Perceived:
    """Чистит текст (невидимые символы, пробелы), режет по длине и запускает таймер."""
    text = clean_text(msg.text)[:max_len]
    return Perceived(message=msg.model_copy(update={"text": text}))


# ---------------------------------------------------------------------------
# Лексикон маркеров категории I
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _load_lexicon() -> dict[str, list[re.Pattern[str]]]:
    with open(DATA_DIR / "lexicon.json", encoding="utf-8") as f:
        raw = json.load(f)
    return {
        group: [re.compile(rf"\b(?:{src})", re.IGNORECASE | re.UNICODE) for src in sources]
        for group, sources in raw["groups"].items()
    }


_DISCOUNT_PCT = re.compile(
    r"(?:скидк\w*[^\d%]{0,25}(\d{1,3}(?:[.,]\d+)?)\s*%|(\d{1,3}(?:[.,]\d+)?)\s*%\s*скидк|"
    r"discount[^\d%]{0,25}(\d{1,3}(?:[.,]\d+)?)\s*%|(\d{1,3}(?:[.,]\d+)?)\s*%\s*discount)",
    re.IGNORECASE,
)


def extract_discount_pct(text: str) -> float | None:
    """Первый запрошенный процент скидки из текста («скидку 15%», «5% скидки»)."""
    match = _DISCOUNT_PCT.search(text)
    if not match:
        return None
    value = next(g for g in match.groups() if g)
    return float(value.replace(",", "."))


def pre_classify(text: str) -> PreClassification:
    """Мгновенная классификация риска: F (инъекция), I (спор/агрессия) или NONE."""
    injection = detect_injection(text)
    if injection.flag:
        legit = injection.legit_text or None
        return PreClassification(
            risk="F",
            markers=[f"injection:{name}" for name in injection.patterns],
            legit_text=legit,
            discount_request_pct=extract_discount_pct(legit) if legit else None,
        )

    cleaned = injection.legit_text  # нормализованный текст без изменений по смыслу
    markers: list[str] = []
    for group, patterns in _load_lexicon().items():
        for pattern in patterns:
            match = pattern.search(cleaned)
            if match:
                markers.append(f"{group}:{match.group(0).lower()}")
    return PreClassification(
        risk="I" if markers else "NONE",
        markers=markers,
        legit_text=cleaned or None,
        discount_request_pct=extract_discount_pct(cleaned),
    )
