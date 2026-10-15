"""Тесты детерминированных шаблонов (тон Ст. 7, честный SLA Ст. 6/8)."""
from datetime import datetime

import pytest

from core import templates
from core.policy import get_manager_sla
from core.schemas import Policy
from core.config import DATA_DIR

FORBIDDEN = ("виноват", "ошибк", "признаём", "признаем", "гарантируем", "наша вина")


@pytest.fixture()
def policy() -> Policy:
    return Policy.model_validate_json((DATA_DIR / "policy_seed.json").read_text(encoding="utf-8"))


def _all_texts(policy: Policy) -> list[str]:
    work = get_manager_sla(policy, datetime(2026, 10, 15, 9, 0))
    night = get_manager_sla(policy, datetime(2026, 10, 15, 20, 0))
    return [
        templates.f_script(), templates.f_script(True), templates.ack_dispute(work),
        templates.ack_dispute(night), templates.interim_ack(), templates.discount_offer(15),
        templates.discount_limit_confirm(10), templates.generic_fallback(),
    ]


def test_no_guilt_admission(policy):
    for text in _all_texts(policy):
        assert not any(word in text.lower() for word in FORBIDDEN), text


def test_ack_uses_honest_sla_from_policy(policy):
    work = templates.ack_dispute(get_manager_sla(policy, datetime(2026, 10, 15, 9, 0)))
    night = templates.ack_dispute(get_manager_sla(policy, datetime(2026, 10, 15, 20, 0)))
    assert "в течение 2 часов" in work and "нерабочее" not in work
    assert "нерабочее время" in night and "в течение 12 часов" in night


def test_sla_phrase_follows_policy_change(policy):
    policy.manager_sla.response_hours = 1
    ack = templates.ack_dispute(get_manager_sla(policy, datetime(2026, 10, 15, 9, 0)))
    assert "в течение 1 часа" in ack


def test_discount_templates_use_given_percent_only():
    assert "10%" in templates.discount_limit_confirm(10)
    assert "12.5%" in templates.discount_offer(12.5)
    assert "15" not in templates.discount_limit_confirm(10)


def test_f_script_mentions_followup_only_when_legit_part():
    assert "следующим сообщением" in templates.f_script(True)
    assert "следующим сообщением" not in templates.f_script(False)


def test_interim_and_fallback_have_no_numbers():
    for text in (templates.interim_ack(), templates.generic_fallback(), templates.f_script()):
        assert not any(ch.isdigit() for ch in text)
