"""Тесты DECISION: приоритет категорий, граница C/I, порог уверенности (чистая логика, без БД)."""
import pytest

from core.config import DATA_DIR
from core.decision import decide
from core.schemas import ConvState, Entities, Policy, PreClassification, Understanding


@pytest.fixture()
def policy() -> Policy:
    p = Policy.model_validate_json((DATA_DIR / "policy_seed.json").read_text(encoding="utf-8"))
    p.version = 1
    return p


def und(cats, conf=0.9, sentiment=0.0, **entities) -> Understanding:
    return Understanding(
        intent="x", category_candidates=cats, confidence=conf, sentiment=sentiment, entities=Entities(**entities)
    )


def pre(risk="NONE", discount=None, markers=()) -> PreClassification:
    return PreClassification(risk=risk, markers=list(markers), discount_request_pct=discount)


def test_priority_g_beats_c_and_a(policy):
    d = decide(pre(), und(["A", "C", "G"]), ConvState(), policy, allowed_pct=12)
    assert d.category == "G" and d.route == "AUTONOMOUS"


@pytest.mark.parametrize("cats,expected", [(["H", "G"], "G"), (["D", "E"], "D"), (["E", "C"], "E"), (["C", "B"], "C"), (["B", "A"], "B")])
def test_priority_order(policy, cats, expected):
    d = decide(pre(), und(cats, people_count=20), ConvState(), policy, allowed_pct=12, has_template=True)
    assert d.category == expected


def test_injection_has_top_priority(policy):
    d = decide(pre("F", markers=["injection:ignore_ru"]), und(["I", "G"]), ConvState(), policy)
    assert d.category == "F" and d.route == "INJECTION"
    assert d.state_updates["suspicious"] is True
    assert decide(pre(), und(["A"]).model_copy(update={"injection_suspected": True}), ConvState(), policy).category == "F"


def test_dispute_by_pre_classifier_and_by_sentiment(policy):
    d = decide(pre("I", markers=["claim:обман"]), und(["A"]), ConvState(), policy)
    assert d.category == "I" and d.route == "SMOOTH_HOLD" and d.state_updates["status"] == "HOLD"
    d2 = decide(pre(), und(["B"], sentiment=-0.8), ConvState(), policy)
    assert d2.route == "SMOOTH_HOLD" and "negative_sentiment" in d2.reasons


def test_low_confidence_clarifies_and_never_goes_to_i(policy):
    d = decide(pre(), und(["B"], conf=0.5), ConvState(), policy)
    assert d.clarify and d.route == "AUTONOMOUS" and d.category == "B"
    assert d.clarify_kind == "generic" and not d.needs_manager_override


def test_low_confidence_llm_dispute_is_ignored_but_pre_classifier_is_not(policy):
    d = decide(pre(), und(["I"], conf=0.5), ConvState(), policy)
    assert d.route == "AUTONOMOUS" and d.clarify


def test_low_confidence_e_means_will_return_and_override_task(policy):
    d = decide(pre(), und(["E"], conf=0.5), ConvState(), policy)
    assert d.category == "E" and d.clarify_kind == "hold_off" and d.needs_manager_override


def test_second_low_confidence_in_a_row_escalates_to_override(policy):
    first = decide(pre(), und([], conf=0.3), ConvState(), policy)
    assert first.clarify_kind == "generic" and first.state_updates["low_conf_streak"] == 1
    second = decide(pre(), und([], conf=0.3), ConvState(low_conf_streak=1), policy)
    assert second.clarify_kind == "hold_off" and second.needs_manager_override


def test_confident_answer_resets_low_conf_streak(policy):
    d = decide(pre(), und(["A"]), ConvState(low_conf_streak=1), policy)
    assert d.state_updates["low_conf_streak"] == 0


# --- Граница C/I -------------------------------------------------------------
def test_discount_within_policy_is_granted(policy):
    d = decide(pre(discount=5), und(["C"]), ConvState(), policy, allowed_pct=12)
    assert d.category == "C" and d.discount_action == "grant" and d.discount_pct == 5


def test_discount_without_number_grants_policy_max(policy):
    d = decide(pre(), und(["C"]), ConvState(), policy, allowed_pct=12)
    assert d.discount_action == "grant" and d.discount_pct == 12


def test_discount_above_max_offer_confirm_then_hold(policy):
    conv = ConvState()
    first = decide(pre(discount=40), und(["C"]), conv, policy, allowed_pct=12)
    assert first.discount_action == "offer_max" and first.discount_pct == 12
    assert first.state_updates["discount_offer_made"] is True

    conv = conv.model_copy(update=first.state_updates)
    second = decide(pre(discount=40), und(["C"]), conv, policy, allowed_pct=12)  # 1-е настаивание
    assert second.discount_action == "confirm_limit" and second.route == "AUTONOMOUS"
    assert second.state_updates["discount_push_count"] == 1

    conv = conv.model_copy(update=second.state_updates)
    third = decide(pre(discount=40), und(["C"]), conv, policy, allowed_pct=12)  # 2-е настаивание
    assert third.route == "SMOOTH_HOLD" and third.category == "I"
    assert third.reasons[0].startswith("discount_push_limit")


def test_push_limit_comes_from_policy(policy):
    policy.thresholds.discount_push_limit = 3
    conv = ConvState(discount_offer_made=True, discount_push_count=1)
    assert decide(pre(discount=40), und(["C"]), conv, policy, allowed_pct=12).route == "AUTONOMOUS"
    conv = ConvState(discount_offer_made=True, discount_push_count=2)
    assert decide(pre(discount=40), und(["C"]), conv, policy, allowed_pct=12).route == "SMOOTH_HOLD"


def test_client_accepting_offer_is_granted(policy):
    conv = ConvState(discount_offer_made=True, discount_push_count=1)
    d = decide(pre(discount=12), und(["C"]), conv, policy, allowed_pct=12)
    assert d.discount_action == "grant"


def test_no_discount_available_by_policy(policy):
    d = decide(pre(), und(["C"]), ConvState(), policy, allowed_pct=0)
    assert d.discount_action == "none_available"
    d2 = decide(pre(discount=10), und(["C"]), ConvState(), policy, allowed_pct=0)
    assert d2.discount_action == "none_available" and d2.state_updates["discount_offer_made"] is True


def test_unknown_order_asks_for_details(policy):
    d = decide(pre(discount=10), und(["C"]), ConvState(), policy, allowed_pct=None)
    assert d.clarify and d.clarify_kind == "order_details"


# --- D -------------------------------------------------------------------------
def test_quote_without_people_count_clarifies(policy):
    d = decide(pre(), und(["D"]), ConvState(), policy)
    assert d.clarify_kind == "people_count"


def test_quote_without_template_goes_to_hold(policy):
    d = decide(pre(), und(["D"], people_count=500), ConvState(), policy, has_template=False)
    assert d.route == "SMOOTH_HOLD" and d.reasons == ["no_quote_template"]


def test_quote_with_template_is_autonomous(policy):
    d = decide(pre(), und(["D"], people_count=40), ConvState(), policy, has_template=True)
    assert d.category == "D" and d.route == "AUTONOMOUS" and not d.clarify


# --- Прочее ------------------------------------------------------------------------
def test_decide_is_pure_and_tracks_max_category(policy):
    conv = ConvState(category="G")
    before = conv.model_dump()
    d = decide(pre(), und(["A"]), conv, policy)
    assert conv.model_dump() == before
    assert d.state_updates["category"] == "G"  # максимум по приоритету за диалог
    assert d.policy_version == 1
