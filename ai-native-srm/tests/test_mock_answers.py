"""Ответы mock-ИИ: человеческий тон, подбор по разделам каталога, честные границы.

Этот блок проверяет именно то, что читает клиент в чате: обращение по имени, живые
формулировки вместо канцелярита, подбор техники из разделов каталога по бюджету,
сравнение моделей и — главное — что mock по-прежнему не выдумывает цифры.
"""

from __future__ import annotations

import pytest

from channels.mock_crm import GREETINGS, THANKS_REPLIES, MockCrm, policy_facts


def client_with_profile(name: str = "Дмитрий Соколов") -> MockCrm:
    crm = MockCrm()
    crm.save_profile({"name": name, "company": "ООО «Ромашка»"})
    return crm


def ask(crm: MockCrm, text: str) -> tuple[str, str]:
    """Ответ mock-ИИ: текст и фаза (FINAL/HOLD)."""
    reply, phase, _hold = crm._answer(text)
    return reply, phase


# --------------------------------------------------------------------------- тон общения
def test_greeting_uses_client_name_and_offers_next_step():
    crm = client_with_profile()
    reply, phase = ask(crm, "Здравствуйте!")
    assert reply.startswith("Дмитрий, ") and phase == "FINAL"
    assert "чем" in reply.lower() or "с чего начнём" in reply.lower()


def test_greeting_without_profile_does_not_invent_name():
    reply, _ = ask(MockCrm(), "Добрый день!")
    assert any(reply.startswith(hello.split("!")[0]) for hello in GREETINGS)
    assert "Дмитрий" not in reply, "без заполненного профиля имени быть не должно"


def test_thanks_and_capabilities_sound_human():
    crm = client_with_profile()
    thanks, _ = ask(crm, "Спасибо, всё получилось!")
    assert thanks in THANKS_REPLIES, "благодарность — один из живых вариантов, а не шаблонный ответ"

    whoami, _ = ask(crm, "Ты бот или человек? Что ты умеешь?")
    assert "ИИ-ассистент" in whoami and "менеджера" in whoami


def test_human_request_goes_to_hold_with_honest_sla():
    crm = client_with_profile()
    crm.chat("Хочу обсудить поставку")  # открываем обращение, чтобы было куда звать менеджера
    reply, phase = ask(crm, "Позовите менеджера, пожалуйста")
    assert phase == "HOLD" and "менеджера" in reply
    assert policy_facts()["sla_phrase"] in reply, "срок ответа — только из Policy Matrix"


# --------------------------------------------------------------------------- подбор техники
def test_pick_by_category_and_budget_lists_catalog_items():
    reply, phase = ask(client_with_profile(), "Нужен ноутбук для бухгалтерии до 80 000")
    assert phase == "FINAL"
    assert "Ноутбуки" in reply
    assert "NB-14L" in reply and "42 900" in reply
    assert "129 900" not in reply, "позиции дороже бюджета предлагать нельзя"


def test_pick_answers_which_category_has_nothing_in_budget():
    reply, _ = ask(client_with_profile(), "Нужен сервер до 50 000")
    assert "Серверы" in reply and "ничего нет" in reply
    assert "199 900" in reply, "вместо выдумки показываем самый доступный вариант из каталога"


def test_pick_by_section_words_without_verb():
    reply, _ = ask(client_with_profile(), "Какие есть коммутаторы?")
    assert "Сетевое оборудование" in reply and "SW-24G" in reply


def test_compare_two_models_shows_prices_from_catalog():
    reply, phase = ask(client_with_profile(), "Что лучше: NB-14B или NB-14P?")
    assert phase == "FINAL"
    assert "74 900" in reply and "129 900" in reply
    assert "Разница в цене — 55 000 ₽" in reply


def test_section_answers_come_from_knowledge_base():
    docs, _ = ask(client_with_profile(), "Какие документы будут и как оплатить?")
    assert "счёт" in docs.lower() and "закрывающие документы" in docs.lower()

    leasing, _ = ask(client_with_profile(), "Можно в лизинг или рассрочку?")
    assert "лизинг" in leasing.lower()


def test_order_number_is_acknowledged_without_inventing_status():
    reply, _ = ask(client_with_profile(), "Где мой заказ ORD-1001?")
    assert "ORD-1001" in reply
    assert "менеджер" in reply.lower()
    assert "доставлен" not in reply.lower(), "mock не выдумывает статус чужого заказа"


def test_service_item_explains_that_dates_are_agreed():
    reply, _ = ask(client_with_profile(), "Расскажите про SRV-PRO")
    assert "услуга" in reply.lower() and "36 000" in reply


@pytest.mark.parametrize(
    "question",
    [
        "Сколько стоит подписка на облачное хранилище?",
        "Можете изготовить мебель под наши размеры?",
        "Do you ship to Kazakhstan?",
    ],
)
def test_unknown_question_goes_to_hold_instead_of_numbers(question: str):
    crm = client_with_profile()
    reply, phase = ask(crm, question)
    assert phase == "HOLD" and "Не хочу называть цифры" in reply
