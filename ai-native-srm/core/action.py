"""ACTION (A–H): tools -> блок FACTS -> LLM-текст -> валидаторы Устава -> BotResponse.

Модуль не обращается к БД: карточка, уведомления и события возвращаются в BotResponse,
сохраняет их оркестратор (core.ai_engine).

Каждый текст для клиента проходит validate_output(): при нарушении — 1 повторная
генерация с подсказкой, затем детерминированный шаблон (render_template).
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from core import rag, templates, tools
from core.guardrails import numbers_in, validate_output
from core.llm import LLMError, LLMProvider
from core.policy import get_manager_sla
from core.prompts.generation import build_generation_prompt, render_template
from core.prompts.system import build_system_prompt
from core.schemas import (
    BotMessage,
    BotResponse,
    ConvState,
    Decision,
    Policy,
    SlaInfo,
    Understanding,
)

GEN_TIMEOUT_S = 2.5
MAX_REPLY_LEN = 1200


def discount_inputs(und: Understanding, text: str) -> dict[str, Any]:
    """Единый способ определить заказ для расчёта скидки (используют и engine, и action)."""
    e = und.entities
    return {"query": e.sku or e.product or text, "qty": e.qty, "budget": e.budget}


def _sla_phrases(policy: Policy) -> list[str]:
    """Обе разрешённые формулировки SLA из Policy (рабочее/нерабочее время) — для валидатора V7."""
    sla = policy.manager_sla
    wh = sla.work_hours
    base = dict(start_hour=wh.start_hour, end_hour=wh.end_hour, days=list(wh.days))
    return [
        templates.sla_phrase(SlaInfo(is_work_time=True, response_hours=sla.response_hours, **base)),
        templates.sla_phrase(SlaInfo(is_work_time=False, response_hours=sla.off_hours_response_hours, **base)),
    ]


def _violations(text: str, numbers: set[str], client_text: str, client_id: int, policy: Policy) -> list[Any]:
    return validate_output(
        text,
        whitelist=numbers,
        client_text=client_text,
        policy=policy,
        foreign_markers=tools.foreign_client_markers(client_id),
        sla_phrases=_sla_phrases(policy),
    )


def _generate(
    scenario: str,
    facts: dict[str, Any],
    text: str,
    client_id: int,
    policy: Policy,
    llm: LLMProvider,
    history: Sequence[tuple[str, str]],
    kb: Sequence[str] = (),
) -> tuple[str, bool, list[dict[str, Any]]]:
    """Генерирует и валидирует ответ. Возвращает (текст, это_шаблон, события)."""
    numbers = numbers_in(facts, list(kb))
    system = build_system_prompt(policy)
    events: list[dict[str, Any]] = []
    hint = ""
    for attempt in (1, 2):
        user = build_generation_prompt(scenario, facts, text, history, kb, hint)
        try:
            candidate = llm.complete_text(system, user, timeout=GEN_TIMEOUT_S, temperature=0.2).strip()
        except LLMError as exc:
            events.append({"type": "llm_error", "payload": {"stage": "generation", "error": type(exc).__name__}})
            break
        found = (
            _violations(candidate, numbers, text, client_id, policy)
            if candidate and len(candidate) <= MAX_REPLY_LEN
            else []
        )
        if candidate and len(candidate) <= MAX_REPLY_LEN and not found:
            return candidate, False, events
        events.append(
            {"type": "validator_block",
             "payload": {"scenario": scenario, "attempt": attempt, "codes": sorted({v.code for v in found}) or ["EMPTY_OR_LONG"]}}
        )
        hint = "; ".join(v.hint for v in found) or "ответ пустой или слишком длинный — дай 2–4 коротких предложения"

    fallback = render_template(scenario, facts, kb)
    if _violations(fallback, numbers, text, client_id, policy):  # страховка: шаблон тоже под проверкой
        events.append({"type": "validator_block", "payload": {"scenario": scenario, "attempt": 3, "codes": ["FALLBACK"]}})
        fallback = templates.generic_fallback()
    return fallback, True, events


def _template_reply(text_out: str, numbers: set[str], client_text: str, client_id: int, policy: Policy) -> tuple[str, list[dict[str, Any]]]:
    """Детерминированный шаблон тоже проходит валидаторы (Ст. 1: «каждое предложение»)."""
    if _violations(text_out, numbers, client_text, client_id, policy):
        return templates.generic_fallback(), [
            {"type": "validator_block", "payload": {"scenario": "template", "attempt": 0, "codes": ["TEMPLATE"]}}
        ]
    return text_out, []


def _response(text: str, deterministic: bool, card: dict[str, Any] | None = None,
              events: list[dict[str, Any]] | None = None,
              notifications: list[dict[str, Any]] | None = None) -> BotResponse:
    return BotResponse(
        messages=[BotMessage(text=text, phase="FINAL", deterministic=deterministic)],
        card_updates={k: v for k, v in (card or {}).items() if v is not None},
        notifications=notifications or [],
        events=events or [],
    )


# ---------------------------------------------------------------------------
# Обработчики категорий
# ---------------------------------------------------------------------------
def _clarify(decision: Decision) -> BotResponse:
    kind = decision.clarify_kind or "generic"
    if kind == "hold_off":
        return _response(
            templates.will_clarify_and_return(), True,
            notifications=[{"type": "OVERRIDE_DUE", "text": "ИИ не уверен в ответе на вопрос клиента — требуется уточнение специалиста."}],
        )
    text = {
        "people_count": templates.ask_people_count,
        "order_details": templates.ask_order_details,
    }.get(kind, templates.generic_fallback)()
    return _response(text, True)


def _discount(decision: Decision, und: Understanding, text: str, client_id: int, policy: Policy,
              llm: LLMProvider, history: Sequence[tuple[str, str]]) -> BotResponse:
    action, pct = decision.discount_action, decision.discount_pct or 0.0
    if action == "grant":
        inputs = discount_inputs(und, text)
        facts = tools.calc_discount(client_id, policy, **inputs)
        total = facts["order_sum"] * (1 - pct / 100) if facts["order_sum"] else None
        gen_facts = {"pct": pct, "product": facts["product"], "qty": facts["qty"], "total_with_discount": total}
        reply, fallback, events = _generate("discount_grant", gen_facts, text, client_id, policy, llm, history)
        return _response(
            reply, fallback,
            card={"discount_pct": pct, "product": facts["product"], "qty": facts["qty"], "stage": "negotiation"},
            events=events,
        )
    if action == "offer_max":
        raw = templates.discount_offer(pct)
    elif action == "confirm_limit":
        raw = templates.discount_limit_confirm(pct)
    else:
        raw = templates.discount_none()
    reply, events = _template_reply(raw, numbers_in(pct), text, client_id, policy)
    return _response(reply, True, card={"stage": "negotiation"}, events=events)


def _quote(und: Understanding, text: str, client_id: int, policy: Policy, llm: LLMProvider,
           history: Sequence[tuple[str, str]]) -> BotResponse:
    quote = tools.build_quote(client_id, und.entities.people_count or 0, policy)
    if quote is None:  # decide() этого не допускает; защита от рассинхрона
        return _response(templates.generic_fallback(), True)
    reply, fallback, events = _generate("quote", quote, text, client_id, policy, llm, history)
    return _response(
        reply, fallback,
        card={"product": quote["name"], "qty": quote["people_count"], "budget": quote["total"], "stage": "quote_sent"},
        events=events,
    )


def _warranty(und: Understanding, text: str, client_id: int, policy: Policy, llm: LLMProvider,
              history: Sequence[tuple[str, str]]) -> BotResponse:
    e = und.entities
    facts = tools.check_warranty(client_id, policy, serial=e.serial, product=e.product or e.sku, order_id=e.order_id)
    scenario = "warranty_found" if facts["order_found"] else "warranty_collect"
    reply, fallback, events = _generate(scenario, facts, text, client_id, policy, llm, history)
    card = (
        {"product": facts["product"], "stage": "warranty_replacement", "notes": f"Замена по заказу {facts['order_id']}"}
        if facts["order_found"]
        else {"stage": "warranty_data_collection"}
    )
    return _response(reply, fallback, card=card, events=events)


def _delivery(und: Understanding, text: str, client_id: int, policy: Policy, llm: LLMProvider,
              history: Sequence[tuple[str, str]]) -> BotResponse:
    facts = tools.delivery_status(client_id, policy, order_id=und.entities.order_id)
    reply, fallback, events = _generate("delivery", facts, text, client_id, policy, llm, history)
    card: dict[str, Any] = {}
    if facts.get("delay_days", 0) > 0:
        card = {"stage": "delivery_delay", "notes": f"Задержка заказа {facts['order_id']}: {facts['delay_days']} дн."}
    return _response(reply, fallback, card=card, events=events)


def _knowledge(decision: Decision, und: Understanding, text: str, client_id: int, policy: Policy,
               llm: LLMProvider, history: Sequence[tuple[str, str]]) -> BotResponse:
    """A (FAQ), B (цена + карточка), E (технический вопрос)."""
    e = und.entities
    entity_query = " ".join(filter(None, [e.sku, e.product]))
    items = (tools.catalog_lookup(client_id, entity_query) if entity_query else []) or tools.catalog_lookup(client_id, text)
    kb = [f"{c.title}\n{c.text}" for c in rag.search(text)]

    if decision.category == "E":
        if not kb:  # в базе знаний ответа нет -> «уточню и вернусь» (Ст. 5, категория E)
            return _clarify(Decision(category="E", route="AUTONOMOUS", clarify=True, clarify_kind="hold_off"))
        reply, fallback, events = _generate("tech_answer", {"items": items[:2]}, text, client_id, policy, llm, history, kb)
        return _response(reply, fallback, events=events)

    reply, fallback, events = _generate("catalog_answer", {"items": items[:3]}, text, client_id, policy, llm, history, kb)
    card: dict[str, Any] = {}
    if decision.category == "B":
        card = {"product": items[0]["name"] if items else e.product, "qty": e.qty, "budget": e.budget, "stage": "inquiry"}
    return _response(reply, fallback, card=card, events=events)


def execute(
    decision: Decision,
    und: Understanding,
    text: str,
    client_id: int,
    conv: ConvState,
    policy: Policy,
    llm: LLMProvider,
    history: Sequence[tuple[str, str]] = (),
) -> BotResponse:
    """Исполняет автономное решение (A–H) и возвращает ответ клиенту + побочные эффекты."""
    if decision.clarify:
        return _clarify(decision)
    category = decision.category
    if category == "C":
        return _discount(decision, und, text, client_id, policy, llm, history)
    if category == "D":
        return _quote(und, text, client_id, policy, llm, history)
    if category == "G":
        return _warranty(und, text, client_id, policy, llm, history)
    if category == "H":
        return _delivery(und, text, client_id, policy, llm, history)
    return _knowledge(decision, und, text, client_id, policy, llm, history)
