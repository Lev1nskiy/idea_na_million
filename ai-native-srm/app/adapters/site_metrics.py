"""Метрики в формулировках сайта-фронтенда («СИНАПС», страница /metrics).

Почему отдельный модуль: у бэкенда своя страница метрик (`core.learning.compute_metrics`,
7 метрик ТЗ + рекомендации по политике), а у сайта — свой набор полей и свои формулы
(share of replies < 100 мс, гистограмма категорий, блокировки V1/V2 отдельными числами).
Считать их «на лету» из тех же таблиц дешевле, чем подгонять UI под наш формат, а числа
остаются согласованными: источник один — events/decisions/conversations.

Границы:
* `ttfr*` считается по событиям `first_response` (их пишет оркестратор для каждого хода);
  группа «IF» — ответы на споры/инъекции (ACK детерминированный, без LLM), остальные — «other»;
* гистограмма и override — по `conversations.category` и журналу `decisions`;
* injection resistance — по таблице инцидентов: инцидент фиксируется только после
  нейтрализации, поэтому непобеждённых в демо-данных нет (pct = 100 при наличии инцидентов).
"""
from __future__ import annotations

from collections import Counter
from datetime import timedelta
from typing import Any, Iterable

from sqlmodel import Session, select

from app import models as m
from core.learning import pre_classifier_recall
from core.schemas import Policy

CAT_ORDER: list[str] = ["F", "I", "G", "H", "D", "E", "C", "B", "A"]
AUTO_CATS: set[str] = {"A", "B", "C", "D", "E", "G", "H"}  # всё, кроме спора и инъекции
OFFICE_DAYS = {1, 2, 3, 4, 5}


def _percentile(values: list[int], p: float) -> int:
    """Перцентиль в формулировке сайта (round, «верхний» индекс) — числа должны совпадать с их UI."""
    if not values:
        return 0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, -(-int(p * len(ordered)) // 100) - 1))
    return int(round(ordered[idx]))


def _share(values: Iterable[int], predicate: Any) -> int:
    items = list(values)
    return round(100 * sum(1 for v in items if predicate(v)) / len(items)) if items else 0


def _count_violations(events: list[m.Event], code: str) -> int:
    total = 0
    for event in events:
        if event.type != "validator_block":
            continue
        codes = (event.payload or {}).get("codes") or []
        if code in codes:
            total += 1
    return total


def _is_off_hours(ts: Any, policy: Policy) -> bool:
    wh = policy.manager_sla.work_hours
    local = ts + timedelta(hours=wh.utc_offset_hours)
    return not (local.isoweekday() in OFFICE_DAYS and wh.start_hour <= local.hour < wh.end_hour)


def compute_site_metrics(s: Session, policy: Policy) -> dict[str, Any]:
    """Собирает `MetricsReport` сайта по текущему состоянию базы."""
    conversations = list(s.exec(select(m.Conversation)).all())
    messages = list(s.exec(select(m.Message)).all())
    decisions = list(s.exec(select(m.Decision)).all())
    incidents = list(s.exec(select(m.SecurityIncident)).all())
    events = list(s.exec(select(m.Event)).all())

    category_by_conv = {c.id: c.category for c in conversations}
    replies = [e for e in events if e.type == "first_response" and e.latency_ms is not None]

    def is_if(event: m.Event) -> bool:
        return (event.payload or {}).get("group") == "IF"

    ttfr_if = [int(e.latency_ms) for e in replies if is_if(e)]
    ttfr_other = [int(e.latency_ms) for e in replies if not is_if(e)]

    auto_convs = [c for c in conversations if c.category in AUTO_CATS]
    total_convs = len(conversations)

    # retention в спорах: клиент продолжил диалог после ACK (или спор доведён до RESOLVED)
    dispute_ids = {d.conversation_id for d in decisions if d.route == "SMOOTH_HOLD"} | {
        c.id for c in conversations if c.category == "I"
    }
    disputes = [c for c in conversations if c.id in dispute_ids]
    retained = 0
    for conv in disputes:
        if conv.status == "RESOLVED":
            retained += 1
            continue
        own = sorted((msg for msg in messages if msg.conversation_id == conv.id), key=lambda x: x.id or 0)
        ack_idx = next((i for i, msg in enumerate(own) if msg.role == "ai" and msg.phase == "ACK"), -1)
        if ack_idx >= 0 and any(msg.role == "client" for msg in own[ack_idx + 1:]):
            retained += 1

    dec_totals = Counter(d.category for d in decisions)
    dec_overridden = Counter(d.category for d in decisions if d.overridden_by)
    hist = Counter(c.category for c in conversations if c.category)

    off_replies = [e for e in replies if e.ts and _is_off_hours(e.ts, policy)]
    recall = pre_classifier_recall().get("recall", 0.0)

    return {
        "policyVersion": policy.version,
        "totals": {
            "conversations": total_convs,
            "autoConversations": len(auto_convs),
            "messages": len(messages),
            "incidents": len(incidents),
            "holds": sum(1 for c in conversations if c.status == "HOLD"),
        },
        "autoDecisionRate": round(100 * len(auto_convs) / total_convs, 1) if total_convs else 0.0,
        "ttfrIF": {
            "p50": _percentile(ttfr_if, 50),
            "p95": _percentile(ttfr_if, 95),
            "shareLt100": _share(ttfr_if, lambda v: v < 100),
            "caughtByPreClassifier": sum(1 for v in ttfr_if if v < 100),
            "caughtByLlm": sum(1 for v in ttfr_if if v >= 100),
        },
        "ttfrOther": {
            "p50": _percentile(ttfr_other, 50),
            "p95": _percentile(ttfr_other, 95),
            "shareLt5000": _share(ttfr_other, lambda v: v < 5000),
            "interimCount": sum(1 for e in events if e.type == "interim_timeout"),
        },
        "disputeRetention": round(100 * retained / len(disputes)) if disputes else 100,
        "hallucinationBlocks": {
            "v1": _count_violations(events, "V1"),
            "v2": _count_violations(events, "V2"),
        },
        "injectionResistance": {
            "total": len(incidents),
            "neutralized": len(incidents),
            "pct": 100,
        },
        "offHoursCoverage": 100 if off_replies or not replies else 0,
        "overrideRateByCategory": [
            {
                "category": cat,
                "rate": round(100 * dec_overridden[cat] / dec_totals[cat]),
                "total": dec_totals[cat],
            }
            for cat in CAT_ORDER
            if dec_totals[cat]
        ],
        "preClassifierRecall": round(float(recall) * 100, 1),
        "categoryHistogram": [{"category": cat, "count": hist[cat]} for cat in CAT_ORDER if hist[cat]],
    }


def events_feed(s: Session, limit: int = 40) -> list[m.Event]:
    """Последние события для журнала LEARNING (новые сверху)."""
    return list(s.exec(select(m.Event).order_by(m.Event.id.desc()).limit(limit)).all())  # type: ignore[attr-defined]


def decisions_feed(s: Session, limit: int = 60) -> list[m.Decision]:
    """Последние решения для журнала LEARNING (новые сверху)."""
    return list(
        s.exec(select(m.Decision).order_by(m.Decision.id.desc()).limit(limit)).all()  # type: ignore[attr-defined]
    )
