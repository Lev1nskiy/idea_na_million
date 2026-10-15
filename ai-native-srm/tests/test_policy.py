"""Тесты Policy Management (Ст. 2): версионирование, отсутствие кэша, расчёты."""
from datetime import datetime

import pytest
from pydantic import ValidationError

from app.models import PolicyVersion
from core.policy import (
    calc_allowed_discount,
    calc_delivery_compensation,
    get_manager_sla,
    get_policy,
    get_policy_version,
    list_policy_versions,
    update_policy,
)


def _bundle_discount() -> float:
    """Один и тот же вызов до и после обновления политики."""
    return calc_allowed_discount(get_policy(), qty=10, order_sum=200_000, client_status="new", is_bundle=True)


def test_seed_policy_created_as_version_1(db):
    policy = get_policy()
    assert policy.version == 1
    assert policy.discounts.max_pct == 15
    assert policy.thresholds.min_confidence == 0.7


def test_max_discount_change_applies_without_restart(db):
    assert _bundle_discount() == 15

    new_policy = update_policy({"discounts": {"max_pct": 10}}, author="admin", note="снижение лимита")

    assert new_policy.version == 2
    assert _bundle_discount() == 10  # тот же вызов, новая политика, без перезапуска
    assert get_policy_version() == 2


def test_versions_are_kept_in_history(db):
    update_policy({"discounts": {"max_pct": 10}}, author="admin")
    versions = list_policy_versions()
    assert [v.version for v in versions] == [2, 1]
    assert versions[1].policy_json["discounts"]["max_pct"] == 15  # v1 не изменилась
    assert versions[0].author == "admin"


def test_get_policy_reads_db_every_time_no_cache(db):
    get_policy()  # прогреваем возможный кэш
    with db.session_scope() as s:
        payload = get_policy(s).model_dump(exclude={"version"})
        payload["discounts"]["max_pct"] = 7
        s.add(PolicyVersion(version=3, policy_json=payload, author="direct-db"))
    assert get_policy().discounts.max_pct == 7
    assert get_policy().version == 3


def test_update_policy_validates_ranges(db):
    with pytest.raises(ValidationError):
        update_policy({"discounts": {"max_pct": 150}}, author="admin")
    assert get_policy_version() == 1  # неудачное обновление версию не создаёт


def test_discount_tier_is_strictly_greater(db):
    """Порог тира работает строго «выше»: ровно на границе скидки ещё нет."""
    policy = get_policy()
    for tier in policy.discounts.tiers:
        at_threshold = calc_allowed_discount(policy, 1, tier.min_sum, "new")
        above = calc_allowed_discount(policy, 1, tier.min_sum + 1, "new")
        assert above >= tier.pct and above >= at_threshold
    smallest = min(policy.discounts.tiers, key=lambda x: x.min_sum)
    assert calc_allowed_discount(policy, 1, smallest.min_sum, "new") < smallest.pct
    assert calc_allowed_discount(policy, 1, smallest.min_sum + 1, "new") == smallest.pct


def test_loyal_bonus_and_cap(db):
    """Лояльный клиент получает бонус поверх тира, но не выше потолка матрицы."""
    policy = get_policy()
    tier = max(policy.discounts.tiers, key=lambda x: x.min_sum)
    expected = min(tier.pct + policy.discounts.loyal_bonus_pct, policy.discounts.max_pct)
    assert calc_allowed_discount(policy, 1, tier.min_sum + 1, "loyal") == expected
    assert calc_allowed_discount(policy, 1, tier.min_sum + 1, "new") == tier.pct

    update_policy({"discounts": {"max_pct": 10}}, author="admin")
    assert calc_allowed_discount(get_policy(), 1, tier.min_sum + 1, "loyal") == 10  # потолок


def test_delivery_compensation_three_days_is_five_percent(db):
    policy = get_policy()
    comp = calc_delivery_compensation(policy, delay_days=3, order_sum=41_900)
    assert comp.pct == 5
    assert comp.amount == pytest.approx(2095.0)
    assert calc_delivery_compensation(policy, delay_days=2, order_sum=41_900).pct == 0


def test_manager_sla_work_vs_off_hours(db):
    policy = get_policy()
    # Четверг 12:00 по Москве (UTC+3) = 09:00 UTC -> рабочее время
    day = get_manager_sla(policy, datetime(2026, 10, 15, 9, 0))
    assert day.is_work_time is True
    assert day.response_hours == policy.manager_sla.response_hours

    # Четверг 23:00 по Москве = 20:00 UTC -> ночь
    night = get_manager_sla(policy, datetime(2026, 10, 15, 20, 0))
    assert night.is_work_time is False
    assert night.response_hours == policy.manager_sla.off_hours_response_hours

    # Суббота 12:00 по Москве -> выходной
    weekend = get_manager_sla(policy, datetime(2026, 10, 17, 9, 0))
    assert weekend.is_work_time is False
