"""LEARNING (MVP): лог событий, метрики из ТЗ и подсказки по политике.

Реального обучения нет: события и решения накапливаются, по ним считаются метрики,
а suggest_policy_changes() предлагает менеджеру пересмотреть Policy.
Все payload'ы событий проходят через mask_pii (Ст. 4).
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from app import models as m
from app.db import session_scope
from core.config import DATA_DIR
from core.perception import pre_classify
from core.policy import get_manager_sla, get_policy
from core.privacy import mask_payload
from core.schemas import utcnow

FAST_REPLY_MS = 100  # Ст. 9: I и F — детерминированный шаблон
SLOW_REPLY_MS = 5000  # Ст. 9: остальные категории
_IF_GROUPS = ("IF_pre_classifier", "IF_llm")


def log_event(
    s: Session, type_: str, conversation_id: int | None, payload: dict[str, Any], latency_ms: int | None = None
) -> None:
    """Пишет событие в журнал; payload маскируется от ПДн."""
    s.add(m.Event(type=type_, conversation_id=conversation_id, payload=mask_payload(payload), latency_ms=latency_ms))


# ---------------------------------------------------------------------------
# Вспомогательное
# ---------------------------------------------------------------------------
def percentile(values: list[float], p: float) -> float | None:
    """Перцентиль (метод ближайшего ранга); None для пустого списка."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return float(ordered[rank - 1])


def _latency_stats(values: list[int], threshold_ms: int) -> dict[str, Any]:
    return {
        "count": len(values),
        "p50_ms": percentile(list(map(float, values)), 50),
        "p95_ms": percentile(list(map(float, values)), 95),
        "threshold_ms": threshold_ms,
        "share_under_threshold": (sum(v < threshold_ms for v in values) / len(values)) if values else None,
    }


def _pct(part: int, total: int) -> float | None:
    return round(100.0 * part / total, 2) if total else None


def pre_classifier_recall() -> dict[str, Any]:
    """Recall и доля ложных срабатываний pre-classifier на размеченном наборе."""
    data = json.loads((DATA_DIR / "preclassifier_testset.json").read_text(encoding="utf-8"))
    missed = [t for t in data["disputes"] if pre_classify(t).risk != "I"]
    false_positive = [t for t in data["neutral"] if pre_classify(t).risk != "NONE"]
    return {
        "recall": round(1 - len(missed) / len(data["disputes"]), 3),
        "false_positive_rate": round(len(false_positive) / len(data["neutral"]), 3),
        "n_disputes": len(data["disputes"]),
        "n_neutral": len(data["neutral"]),
        "missed": missed,
    }


# ---------------------------------------------------------------------------
# Метрики
# ---------------------------------------------------------------------------
def compute_metrics(since: datetime | None = None, session: Session | None = None) -> dict[str, Any]:
    """Метрики ТЗ: TTFR, auto-decision rate, retention, price hallucination, injection resistance,
    off-hours coverage, время ответа менеджера + override_rate и pre_classifier_recall."""
    if session is not None:
        return _compute(session, since)
    with session_scope() as own:
        return _compute(own, since)


def _compute(s: Session, since: datetime | None) -> dict[str, Any]:
    def after(model: Any, column: Any) -> list[Any]:
        stmt = select(model)
        if since is not None:
            stmt = stmt.where(column >= since)
        return list(s.exec(stmt).all())

    events = after(m.Event, m.Event.ts)
    convs = after(m.Conversation, m.Conversation.created_at)
    conv_ids = {c.id for c in convs}
    decisions = [d for d in after(m.Decision, m.Decision.ts) if d.conversation_id in conv_ids or since is None]
    handoffs = after(m.Handoff, m.Handoff.created_at)
    ai_messages = [x for x in after(m.Message, m.Message.ts) if x.role == "ai"]
    incidents = after(m.SecurityIncident, m.SecurityIncident.ts)
    policy = get_policy(s)
    now = utcnow()

    # 1. Time-to-first-response (раздельно для I/F, hold-ответов и остальных)
    groups: dict[str, list[int]] = defaultdict(list)
    for e in events:
        if e.type != "first_response" or e.latency_ms is None:
            continue
        group = e.payload.get("group", "other")
        if group == "IF":
            group = "IF_llm" if e.payload.get("caught_by") == "llm" else "IF_pre_classifier"
        groups[group].append(e.latency_ms)
    ttfr = {
        "IF_pre_classifier": _latency_stats(groups["IF_pre_classifier"], FAST_REPLY_MS),
        "IF_llm": _latency_stats(groups["IF_llm"], FAST_REPLY_MS),
        "hold_followups": _latency_stats(groups["hold"], SLOW_REPLY_MS),
        "other": _latency_stats(groups["other"], SLOW_REPLY_MS),
        "interim_count": sum(e.type == "interim_timeout" for e in events),
    }

    # 2. Auto-decision rate: (диалоги A–H) / (все диалоги) × 100; диалог с хотя бы одним Smooth & Hold — это I
    dispute_ids = {d.conversation_id for d in decisions if d.route == "SMOOTH_HOLD"} & conv_ids
    auto = len(convs) - len(dispute_ids)
    auto_rate = _pct(auto, len(convs))

    # 3. Retention in disputes (прокси: доля споров, где клиент не помечен как ушедший — статус LOST)
    disputes = [c for c in convs if c.id in dispute_ids]
    lost = [c for c in disputes if c.status == "LOST"]
    retention = {
        "retention_pct": _pct(len(disputes) - len(lost), len(disputes)),
        "disputes": len(disputes),
        "still_holding": sum(c.status == "HOLD" for c in disputes),
        "handed_off": sum(c.status in ("HANDOFF", "RESOLVED") for c in disputes),
        "lost": len(lost),
        "basis": "proxy: статус LOST ставит канал/менеджер; сигнала ухода клиента в прототипе нет",
    }

    # 4. Price hallucination: перехваченные V1/V2; в доставленных ответах — 0 (validator_bypass не фиксировался)
    blocked = [e for e in events if e.type == "validator_block" and {"V1", "V2"} & set(e.payload.get("codes", []))]
    llm_replies = sum(1 for x in ai_messages if not x.deterministic)
    price = {
        "intercepted": len(blocked),
        "llm_replies": llm_replies,
        "delivered_hallucinations": sum(e.type == "validator_bypass" for e in events),
        "delivered_rate_pct": 0.0 if not any(e.type == "validator_bypass" for e in events) else None,
        "note": "числа вне белого списка блокируются валидатором V1/V2 до отправки клиенту",
    }

    # 5. Injection resistance: доля «инъекционных» диалогов без нарушения (скидка выше максимума в карточке)
    f_convs = [c for c in convs if c.suspicious]
    deals = {d.conversation_id: d for d in after(m.Deal, m.Deal.created_at)}
    breaches = [
        c for c in f_convs
        if (deal := deals.get(c.id)) is not None and (deal.discount_pct or 0) > policy.discounts.max_pct
    ]
    injection = {
        "resistance_pct": _pct(len(f_convs) - len(breaches), len(f_convs)),
        "conversations": len(f_convs),
        "incidents": len(incidents),
        "breaches": len(breaches),
    }

    # 6. Off-hours coverage: сообщения вне рабочего времени, получившие ответ ≤ 5 с
    received = {e.payload["message_id"]: e.ts for e in events if e.type == "message_received" and "message_id" in e.payload}
    answered = {e.payload["message_id"]: e.latency_ms for e in events if e.type == "first_response" and "message_id" in e.payload}
    silent = {e.payload["message_id"] for e in events if e.type == "handoff_silent" and "message_id" in e.payload}
    off_total = off_ok = all_total = all_ok = 0
    for mid, ts in received.items():
        if mid in silent:  # менеджер ведёт диалог — ИИ молчит по протоколу
            continue
        ok = answered.get(mid) is not None and answered[mid] <= SLOW_REPLY_MS
        all_total += 1
        all_ok += ok
        if not get_manager_sla(policy, ts).is_work_time:
            off_total += 1
            off_ok += ok
    coverage = {
        "off_hours_pct": _pct(off_ok, off_total),
        "off_hours_messages": off_total,
        "all_messages_answered_within_5s_pct": _pct(all_ok, all_total),
        "all_messages": all_total,
    }

    # 7. Время ответа менеджера на споры
    waits = [(h.taken_at - h.created_at).total_seconds() / 60 for h in handoffs if h.taken_at is not None]
    pending = [(now - h.created_at).total_seconds() / 60 for h in handoffs if h.status == "PENDING"]
    manager = {
        "taken": len(waits),
        "p50_min": percentile(waits, 50),
        "p95_min": percentile(waits, 95),
        "waiting_now": len(pending),
        "max_waiting_min": round(max(pending), 1) if pending else None,
    }

    # override_rate по категориям
    totals: Counter[str] = Counter(d.category for d in decisions)
    overridden: Counter[str] = Counter(d.category for d in decisions if d.overridden_by)
    override = {
        "overall_pct": _pct(sum(overridden.values()), sum(totals.values())),
        "by_category": {c: {"decisions": totals[c], "overridden": overridden[c], "rate_pct": _pct(overridden[c], totals[c])} for c in sorted(totals)},
    }

    # доля споров, пойманных pre-classifier (против «пойманных LLM»)
    caught = Counter(e.payload.get("caught_by") for e in events if e.type == "dispute_hold")

    result = {
        "generated_at": now.isoformat(),
        "conversations": len(convs),
        "ttfr": ttfr,
        "auto_decision_rate_pct": auto_rate,
        "client_retention_in_disputes": retention,
        "price_hallucination": price,
        "injection_resistance": injection,
        "off_hours_coverage": coverage,
        "manager_response_time": manager,
        "override_rate": override,
        "pre_classifier_recall": pre_classifier_recall(),
        "disputes_caught_by": {"pre_classifier": caught["pre_classifier"], "llm": caught["llm"]},
    }
    result["policy_suggestions"] = _suggest(result, decisions)
    return result


def _suggest(metrics: dict[str, Any], decisions: list[Any]) -> list[str]:
    """Подсказки менеджеру по политике (MVP вместо обучения)."""
    tips: list[str] = []
    c_stats = metrics["override_rate"]["by_category"].get("C")
    if c_stats and c_stats["decisions"] >= 5 and (c_stats["rate_pct"] or 0) >= 20:
        tips.append(f"Менеджеры отменяют {c_stats['rate_pct']:g}% решений по скидкам — пересмотрите лимиты скидок в Policy.")
    disputes = [d for d in decisions if d.route == "SMOOTH_HOLD"]
    pushed = [d for d in disputes if any(str(r).startswith("discount_push_limit") for r in (d.reasons or []))]
    if len(disputes) >= 3 and len(pushed) / len(disputes) >= 0.3:
        tips.append("Много споров из-за скидки выше максимума — рассмотрите повышение лимита или бонусную remedy-опцию.")
    ttfr = metrics["ttfr"]
    replies = sum(ttfr[g]["count"] for g in ("other", "hold_followups")) or 1
    if ttfr["interim_count"] / replies > 0.2:
        tips.append("Часто срабатывает промежуточный ответ (медленная LLM) — проверьте провайдера и таймауты.")
    if metrics["disputes_caught_by"]["llm"] > metrics["disputes_caught_by"]["pre_classifier"]:
        tips.append("Большинство споров ловит только LLM — расширьте data/lexicon.json маркерами.")
    return tips
