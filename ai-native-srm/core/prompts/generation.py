"""Промпты генерации ответов (A–H) и детерминированные «аварийные» тексты.

render_template() — единый источник текста по сценарию и факту: им пользуются
(1) action.py как fallback, если LLM не прошла валидацию, и (2) MockLLM как «модель».
Числа здесь подставляются только из facts (Ст. 2).
"""
from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from core import templates

HISTORY_LIMIT = 4
HISTORY_ITEM_MAX = 300
_TAGS = re.compile(r"</?\s*(?:client_message|history|facts|kb)\s*>", re.IGNORECASE)

SCENARIO_GUIDE: dict[str, str] = {
    "catalog_answer": (
        "Ответь на вопрос клиента о товарах, ценах, наличии и сроках, используя ТОЛЬКО позиции из <facts>. "
        "Если подходящих позиций нет — вежливо попроси уточнить модель."
    ),
    "tech_answer": (
        "Ответь на технический вопрос, используя ТОЛЬКО сведения из <kb> и <facts>. "
        "Если ответа там нет — скажи «уточню и вернусь», ничего не придумывая."
    ),
    "discount_grant": "Подтверди скидку из <facts> и коротко назови итоговую сумму.",
    "quote": "Представь коммерческое предложение: перечисли позиции из <facts> и итог. Упомяни возможную скидку при комплекте.",
    "warranty_found": (
        "Сообщи, что заказ подтверждён и оформляется замена в срок из <facts>. "
        "Прояви сочувствие, но не признавай вину компании."
    ),
    "warranty_collect": (
        "Заказ не найден: вежливо попроси номер заказа или серийный номер и дату покупки. "
        "НЕ отказывай и не называй сроков замены."
    ),
    "delivery": (
        "Сообщи статус доставки и, если есть, компенсацию по политике из <facts>. "
        "Прояви сочувствие к задержке, не признавая вину."
    ),
    "smooth_hold": (
        "Ты удерживаешь клиента до подключения менеджера (Smooth & Hold). ОБЯЗАТЕЛЬНО: прояви эмпатию и "
        "извинись от имени компании БЕЗ признания вины; дословно приведи фразу о сроке из facts.sla_phrase; "
        "remedy_options упомяни только как ориентир («возможные варианты — …, окончательное решение примет "
        "менеджер»); если клиент задал нейтральный уточняющий вопрос — ответь коротко. "
        "ЗАПРЕЩЕНО: решать вопрос по существу, обещать конкретное решение или компенсацию, признавать вину, "
        "спорить, называть другие сроки связи с менеджером."
    ),
}


def sanitize(text: str) -> str:
    """Убирает из текста служебные теги, чтобы клиент не мог «закрыть» блок данных."""
    return _TAGS.sub("", text)


def build_generation_prompt(
    scenario: str,
    facts: dict[str, Any],
    client_text: str,
    history_tail: Sequence[tuple[str, str]] = (),
    kb_snippets: Sequence[str] = (),
    hint: str = "",
) -> str:
    """user-сообщение для генерации: история, сообщение клиента, FACTS и база знаний."""
    lines = [
        f"{role}: {sanitize(msg)[:HISTORY_ITEM_MAX]}" for role, msg in list(history_tail)[-HISTORY_LIMIT:]
    ]
    parts = []
    if lines:
        parts.append("<history>\n" + "\n".join(lines) + "\n</history>")
    parts.append(f"<client_message>\n{sanitize(client_text)}\n</client_message>")
    parts.append("<facts>" + json.dumps({"scenario": scenario, **facts}, ensure_ascii=False) + "</facts>")
    if kb_snippets:
        parts.append("<kb>\n" + "\n---\n".join(kb_snippets) + "\n</kb>")
    parts.append(
        f"Задача: {SCENARIO_GUIDE.get(scenario, 'Ответь клиенту.')} "
        "Числа — ТОЛЬКО из <facts> и <kb>, ничего не округляй и не придумывай. "
        "Ответ: 2–4 коротких предложения (для списков — строки с «•»), без markdown и без упоминания служебных тегов."
    )
    if hint:
        parts.append(f"ИСПРАВЬ предыдущий ответ: {hint}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Детерминированные тексты по сценарию
# ---------------------------------------------------------------------------
def _money(value: float) -> str:
    value = round(float(value), 2)
    if value == int(value):
        return f"{int(value):,}".replace(",", " ") + " ₽"
    return f"{value:,.2f}".replace(",", " ").replace(".", ",") + " ₽"


def _days(n: int) -> str:
    word = "день" if n % 10 == 1 and n % 100 != 11 else ("дня" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "дней")
    return f"{n} {word}"


def _item_line(item: dict[str, Any]) -> str:
    stock = item.get("stock_qty")
    if stock is None:
        availability = "услуга"
    elif stock > 0:
        availability = f"в наличии ({stock} шт.)"
    else:
        availability = f"под заказ, срок поставки {_days(int(item.get('lead_time_days') or 0))}"
    return f"• {item['name']} — {_money(item['price'])}, {availability}"


def _first_sentences(text: str, limit: int = 450) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    return cut[: cut.rfind(". ") + 1] if ". " in cut else cut


def render_template(scenario: str, facts: dict[str, Any], kb_snippets: Sequence[str] = ()) -> str:
    """Текст ответа по сценарию без LLM (fallback и режим MockLLM)."""
    if scenario == "catalog_answer":
        items = facts.get("items") or []
        if not items:
            return (
                "Подскажите, пожалуйста, что именно ищете — модель, тип техники или задачу "
                "(например, «нужен ноутбук для бухгалтерии»), и я подберу варианты из каталога."
            )
        lines = "\n".join(_item_line(i) for i in items[:3])
        tail = (
            "Скажите количество и задачу — посчитаю итог со скидкой."
            if len(items) >= 1
            else ""
        )
        return f"Посмотрел каталог — вот что подходит:\n{lines}\n{tail}".rstrip()

    if scenario == "tech_answer":
        if kb_snippets:
            return "Расскажу, как это работает у нас: " + _first_sentences(kb_snippets[0])
        return templates.will_clarify_and_return()

    if scenario == "discount_grant":
        pct = f"{facts['pct']:g}"
        if facts.get("product"):
            return (
                f"Оформляю скидку {pct}% на {facts['qty']} × {facts['product']}: "
                f"итоговая сумма заказа {_money(facts['total_with_discount'])}. "
                "Счёт и договор подготовит менеджер — передать ему заявку?"
            )
        return f"Оформляю скидку {pct}% на заказ. Уточните, пожалуйста, позицию и количество — сразу посчитаю итог."

    if scenario == "quote":
        lines = "\n".join(
            f"• {ln['name']} × {ln['qty']} — {_money(ln['line_total'])}" for ln in facts["lines"]
        )
        text = (
            f"Собрал коммерческое предложение «{facts['name']}» на {facts['people_count']} человек:\n"
            f"{lines}\nИтого: {_money(facts['total'])}."
        )
        if facts.get("bundle_max_pct"):
            text += (
                f"\nПри оформлении комплекта возможна скидка до {facts['bundle_max_pct']:g}% — "
                "скажите, и оформлю её. Если захотите что-то заменить или добавить, пересоберу комплект."
            )
        return text

    if scenario == "warranty_found":
        return (
            f"Сожалею, что техника подвела. Заказ {facts['order_id']} от {facts['order_date']} нашёл: "
            f"оформляю замену {facts['product']} — это займёт {templates._hours_phrase(int(facts['replace_hours']))}, "
            "документы собирать не нужно, всё есть у нас. Если удобнее в другое время, напишите — согласуем."
        )

    if scenario == "warranty_collect":
        return (
            "Сожалею, что техника подвела, — это неприятно. Ваш заказ не удалось найти в истории: "
            "пришлите, пожалуйста, номер заказа или серийный номер устройства и дату покупки — "
            "уточню данные и сразу оформлю замену."
        )

    if scenario == "delivery":
        if not facts.get("order_found"):
            return "Назовите, пожалуйста, номер заказа — проверю его статус и срок доставки."
        base = f"Посмотрел заказ {facts['order_id']}: статус — {facts['status_text']}."
        delay = int(facts.get("delay_days", 0))
        if delay <= 0:
            return base + " Задержек нет, всё идёт по плану."
        text = f"{base} Сожалею, что доставка сдвинулась на {_days(delay)}."
        if facts.get("compensation_pct", 0) > 0:
            text += (
                f" По политике компании предусмотрена компенсация — {facts['compensation_pct']:g}% "
                f"от суммы заказа, то есть {_money(facts['compensation_amount'])}; оформляю её."
            )
        return text

    if scenario == "smooth_hold":
        return templates.smooth_fallback(facts["sla_phrase"], facts.get("remedy_options") or [])

    return templates.generic_fallback()
