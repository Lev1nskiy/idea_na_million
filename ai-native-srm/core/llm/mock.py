"""MockLLM: детерминированная «модель» на ключевых словах.

Работает без ключей и сети: для тестов, офлайн-демо и как последний элемент
fallback-цепочки. Понимает формат промпта из core/prompts/understanding.py
(текст клиента внутри <client_message>…</client_message>).
"""
from __future__ import annotations

import json
import re
from typing import Any

from core.llm.base import DEFAULT_TIMEOUT_S

_CLIENT_MSG = re.compile(r"<client_message>(.*?)</client_message>", re.DOTALL)
_FACTS = re.compile(r"<facts>(.*?)</facts>", re.DOTALL)
_KB = re.compile(r"<kb>\n?(.*?)\n?</kb>", re.DOTALL)

_INJECTION = ("игнорир", "ignore", "забудь", "system prompt", "системн", "ты теперь", "jailbreak")
_DISPUTE = (
    "обман", "мошен", "в суд", "претензи", "верните деньги", "жалоб", "ужасн",
    "безобраз", "идиот", "хамств", "к конкурент", "дорого",
)
_WARRANTY = ("сломал", "не работает", "не включа", "брак", "неисправ", "не печата", "гарант",
             "перестал", "серийн", "замените")
_DELIVERY = ("доставк", "привез", "привоз", "задерж", "где мой заказ", "курьер", "опоздал", "жду", "ждём", "ждем")
_QUOTE = ("комплект", "коммерческ", "кп ", "кп,", "сотрудник")
_PURCHASE = ("куп", "оформ", "докупить", "заказать", "заявк", "интересует", "хотим взять", "берём", "берем")
_NEED = ("нужн", "нужно", "нужны", "надо")
_TECH = ("совместим", "1с", "драйвер", "windows", "linux", "macos", "подключ")
_PRICE = ("сколько стоит", "цена", "цену", "стоимост", "прайс", "почём", "почем")
_FAQ = ("есть ли", "в наличии", "наличи", "срок", "когда", "какой", "какая", "какие")
_NEGATIVE = ("плохо", "недовол", "разочарова", "ужас")

_RANK = {"F": 0, "I": 1, "G": 2, "H": 2, "D": 3, "E": 4, "C": 5, "B": 6, "A": 7}
# Серийники (SN-…), номера заказов (ORD-…/ЗК-…) и SKU каталога в любом формате.
# Промпт понимания требует эти поля от модели — мок обязан их отдавать так же.
_SERIAL = re.compile(r"\bSN-[A-Z0-9]+(?:-[A-Z0-9]+)*\b", re.IGNORECASE)
_ORDER_ID = re.compile(r"\b(?:ORD|ЗК)-\s?(\d{3,5})\b", re.IGNORECASE)
_SKU = re.compile(r"\b(?!SN-|ORD-)[A-Z]{2,4}-[A-Z0-9]{2,6}(?:-[A-Z0-9]+)*\b")
_PCT = re.compile(r"(\d{1,3}(?:[.,]\d+)?)\s*%")
_QTY = re.compile(r"(\d+)\s*(?:шт|штук|единиц|принтер|мфу|картридж)", re.IGNORECASE)
_PEOPLE = re.compile(r"(\d+)\s*(?:человек|сотрудник|чел\b)", re.IGNORECASE)
_PRODUCT = re.compile(
    r"(мфу|принтер\w*|сканер\w*|картридж\w*|тонер\w*)(?:\s+([A-Za-z0-9\-]{2,}))?", re.IGNORECASE
)


def _has(text: str, markers: tuple[str, ...]) -> bool:
    return any(m in text for m in markers)


def _mock_understanding(text: str) -> dict[str, Any]:
    t = text.lower()
    cats: dict[str, str] = {}  # категория -> intent
    if _has(t, _INJECTION):
        cats["F"] = "prompt_injection"
    if _has(t, _DISPUTE):
        cats["I"] = "dispute"
    if _has(t, _WARRANTY):
        cats["G"] = "warranty_claim"
    if _has(t, _DELIVERY) or _ORDER_ID.search(text):  # конкретный номер заказа = вопрос о его статусе
        cats["H"] = "delivery_status"
    if _has(t, _QUOTE) or _PEOPLE.search(t):
        cats["D"] = "custom_quote"
    if _has(t, _PURCHASE) or (_has(t, _NEED) and _QTY.search(t)):
        cats["B"] = "purchase_request"
    if _has(t, _TECH):
        cats["E"] = "technical_question"
    if "скидк" in t:
        cats["C"] = "discount_request"
    if _has(t, _PRICE):
        cats["B"] = "price_inquiry"
    if _has(t, _FAQ):
        cats["A"] = "faq"

    entities: dict[str, Any] = {}
    if (serial := _SERIAL.search(text)):
        entities["serial"] = serial.group(0).upper()
    if (ordid := _ORDER_ID.search(text)):
        prefix = "ЗК" if ordid.group(0).upper().startswith("ЗК") else "ORD"
        entities["order_id"] = f"{prefix}-{ordid.group(1)}"
    if (sku := _SKU.search(text.upper())):
        entities["sku"] = sku.group(0).upper()
        entities["product"] = entities["sku"]
    elif (prod := _PRODUCT.search(text)):
        entities["product"] = " ".join(p for p in prod.groups() if p)
    if (pct := _PCT.search(t)):
        entities["discount_pct"] = float(pct.group(1).replace(",", "."))
    if (qty := _QTY.search(t)):
        entities["qty"] = int(qty.group(1))
    if (people := _PEOPLE.search(t)):
        entities["people_count"] = int(people.group(1))

    ordered = sorted(cats, key=lambda c: _RANK[c])
    sentiment = -0.8 if "I" in cats else (-0.5 if _has(t, _NEGATIVE) else 0.0)
    if not ordered or len(t.strip()) < 4:
        confidence = 0.3 if ordered == [] else 0.5
    elif "E" in cats:
        confidence = 0.85
    else:
        confidence = 0.9

    return {
        "intent": cats[ordered[0]] if ordered else "unknown",
        "category_candidates": ordered,
        "entities": entities,
        "sentiment": sentiment,
        "confidence": confidence,
        "injection_suspected": "F" in cats,
        "language": "ru",
    }


class MockLLM:
    name = "mock"

    def complete_json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        match = _CLIENT_MSG.search(user)
        if match is None:
            return {}
        return _mock_understanding(match.group(1))

    def complete_text(
        self,
        system: str,
        user: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
        temperature: float = 0.0,
    ) -> str:
        facts_match = _FACTS.search(user)
        if facts_match is None:
            return "Спасибо за обращение! Передал информацию, скоро вернусь с ответом."
        # «Генерация» по блоку FACTS: тот же детерминированный текст, что и аварийный fallback.
        from core.prompts.generation import render_template  # локально: избегаем циклического импорта

        try:
            facts = json.loads(facts_match.group(1))
        except json.JSONDecodeError:
            return "Спасибо за обращение! Передал информацию, скоро вернусь с ответом."
        kb_match = _KB.search(user)
        kb_snippets = [s.strip() for s in kb_match.group(1).split("\n---\n")] if kb_match else []
        return render_template(str(facts.get("scenario", "")), facts, kb_snippets)
