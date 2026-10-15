"""DECISION: категория, граница C/I, порог уверенности, маршрут.

decide() — чистая функция: ничего не читает из БД и ничего не пишет. Всё нужное
(Policy, состояние диалога, допустимая скидка, наличие шаблона КП) передаётся
аргументами; изменения состояния возвращаются в Decision.state_updates.

Приоритет категорий: F > I > G/H > D > E > C > B > A (core.schemas.CATEGORY_RANK).
"""
from __future__ import annotations

from typing import Any

from core.schemas import (
    CATEGORY_RANK,
    ConvState,
    Decision,
    Policy,
    PreClassification,
    Understanding,
    top_category,
)

_EPS = 1e-9


def _hold(
    category_state: str | None,
    policy: Policy,
    reasons: list[str],
    updates: dict[str, Any] | None = None,
) -> Decision:
    """Маршрут Smooth & Hold (категория I)."""
    state = {"status": "HOLD", "category": top_category(category_state, "I"), **(updates or {})}
    return Decision(
        category="I",
        route="SMOOTH_HOLD",
        reasons=reasons,
        policy_version=policy.version,
        state_updates=state,
    )


def decide(
    pre: PreClassification,
    und: Understanding,
    conv: ConvState,
    policy: Policy,
    *,
    allowed_pct: float | None = None,
    has_template: bool | None = None,
) -> Decision:
    """Решает, кто и как обрабатывает сообщение.

    allowed_pct — максимально допустимая скидка по актуальной Policy для заказа
    (None — заказ не определён); has_template — есть ли шаблон КП (None — неизвестно).
    """
    th = policy.thresholds
    version = policy.version

    # --- F: инъекция (Ст. 3) -------------------------------------------------
    if pre.risk == "F" or und.injection_suspected or "F" in und.category_candidates:
        reasons = ["injection:pre_classifier"] if pre.risk == "F" else ["injection:llm"]
        return Decision(
            category="F",
            route="INJECTION",
            reasons=reasons,
            policy_version=version,
            state_updates={"suspicious": True, "category": top_category(conv.category, "F")},
        )

    low_conf = und.confidence < th.min_confidence

    # --- I: спор / агрессия (Ст. 6) -------------------------------------------
    hold_reasons: list[str] = []
    if pre.risk == "I":
        hold_reasons.append("pre_classifier:" + ",".join(pre.markers[:3]))
    if und.sentiment < th.negative_sentiment:
        hold_reasons.append("negative_sentiment")
    if "I" in und.category_candidates and not low_conf:
        hold_reasons.append("llm_dispute")
    if hold_reasons:
        return _hold(conv.category, policy, hold_reasons)

    candidates = [c for c in und.category_candidates if c in CATEGORY_RANK]
    # G и H равны по приоритету (G/H): при равенстве берём G — порядок детерминирован
    top = min(candidates, key=lambda c: (CATEGORY_RANK[c], c)) if candidates else None

    # --- Низкая уверенность: уточняем, а НЕ уходим в I (roadmap п.0 №8) ---------
    if low_conf:
        streak = conv.low_conf_streak + 1
        hold_off = top == "E" or streak >= 2  # «уточню и вернусь» + задача менеджеру
        category = top or ("E" if hold_off else "B")
        return Decision(
            category=category,  # type: ignore[arg-type]
            route="AUTONOMOUS",
            clarify=True,
            clarify_kind="hold_off" if hold_off else "generic",
            needs_manager_override=hold_off,
            reasons=["low_confidence"] + (["repeated_low_confidence"] if streak >= 2 else []),
            policy_version=version,
            state_updates={"low_conf_streak": streak, "category": top_category(conv.category, category)},
        )

    updates: dict[str, Any] = {"low_conf_streak": 0}

    # --- Нет категории при высокой уверенности (приветствие и т.п.) -----------
    if top is None:
        return Decision(
            category="B",
            route="AUTONOMOUS",
            clarify=True,
            clarify_kind="generic",
            reasons=["no_category"],
            policy_version=version,
            state_updates={**updates, "category": top_category(conv.category, "B")},
        )

    # --- D: нестандартное КП ----------------------------------------------------
    if top == "D":
        if und.entities.people_count is None:
            return Decision(
                category="D", route="AUTONOMOUS", clarify=True, clarify_kind="people_count",
                reasons=["need_people_count"], policy_version=version,
                state_updates={**updates, "category": top_category(conv.category, "D")},
            )
        if has_template is False:
            return _hold(conv.category, policy, ["no_quote_template"], updates)

    # --- C: скидка, граница C/I (roadmap п.0 №3, №16) --------------------------
    discount: dict[str, Any] = {}
    if top == "C":
        if allowed_pct is None:
            return Decision(
                category="C", route="AUTONOMOUS", clarify=True, clarify_kind="order_details",
                reasons=["need_order_details"], policy_version=version,
                state_updates={**updates, "category": top_category(conv.category, "C")},
            )
        requested = pre.discount_request_pct
        if requested is None:
            requested = und.entities.discount_pct

        if allowed_pct > _EPS and (requested is None or requested <= allowed_pct + _EPS):
            discount = {"discount_action": "grant", "discount_pct": requested if requested is not None else allowed_pct}
        elif requested is None:  # просит скидку вообще, а по политике её нет
            discount = {"discount_action": "none_available", "discount_pct": 0.0}
        elif not conv.discount_offer_made:  # запрос выше максимума: ОДНО предложение максимума
            updates["discount_offer_made"] = True
            discount = {
                "discount_action": "offer_max" if allowed_pct > _EPS else "none_available",
                "discount_pct": allowed_pct,
            }
        else:  # клиент настаивает
            push = conv.discount_push_count + 1
            updates["discount_push_count"] = push
            if push >= th.discount_push_limit:
                return _hold(conv.category, policy, [f"discount_push_limit:{push}"], updates)
            discount = {"discount_action": "confirm_limit", "discount_pct": allowed_pct}

    updates["category"] = top_category(conv.category, top)
    return Decision(
        category=top,  # type: ignore[arg-type]
        route="AUTONOMOUS",
        reasons=[f"category:{top}"],
        policy_version=version,
        state_updates=updates,
        **discount,
    )
