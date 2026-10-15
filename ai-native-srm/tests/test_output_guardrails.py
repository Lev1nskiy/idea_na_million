"""Тесты выходных валидаторов (V1–V5, V7) и защитного цикла генерации в action."""
import pytest

from core import action, templates
from core.config import DATA_DIR
from core.guardrails import canonical_number, extract_numbers, numbers_in, validate_output
from core.llm import LLMError
from core.prompts.system import build_system_prompt
from core.schemas import ConvState, Decision, Entities, Policy, Understanding


@pytest.fixture()
def policy() -> Policy:
    p = Policy.model_validate_json((DATA_DIR / "policy_seed.json").read_text(encoding="utf-8"))
    p.version = 1
    return p


def codes(text, **kw) -> set[str]:
    kw.setdefault("whitelist", set())
    return {v.code for v in validate_output(text, **kw)}


# --- V1 ---------------------------------------------------------------------------
def test_v1_number_outside_whitelist_blocked():
    assert codes("Цена 38 900 ₽", whitelist={"38900"}) == set()
    assert "V1" in codes("Цена 39 900 ₽", whitelist={"38900"})


def test_v1_client_percent_is_not_whitelisted_but_quantity_is():
    # «99%» из сообщения клиента эхом пройти не должно; «10 штук» — можно
    assert "V1" in codes("Хорошо, оформляю скидку 99%", client_text="Дайте скидку 99%")
    assert codes("Оформляю 10 штук", client_text="Хочу 10 штук") == set()
    assert "V1" in codes("Оформляю 99 процентов", client_text="скидка 99 процентов")


def test_v1_identifiers_are_not_numbers():
    text = "МФУ PX-M4200 совместим с 1С, формат A4, заказ ORD-1001"
    assert codes(text) == set()


def test_number_helpers():
    assert canonical_number("38 900") == "38900"
    assert canonical_number("2095,50") == "2095.5"
    assert canonical_number("15.0") == "15"
    assert extract_numbers("скидка 5% и 3 шт") == [("5", True), ("3", False)]
    assert numbers_in({"a": 12.5, "b": [38900, "заказ от 20.09.2026"], "c": None, "d": True}) >= {"12.5", "38900", "20.09"}


def test_v1_dates_roundtrip_through_facts():
    facts = {"order_date": "20.09.2026"}
    assert codes("Заказ от 20.09.2026", whitelist=numbers_in(facts)) == set()


# --- V2 ---------------------------------------------------------------------------
def test_v2_discount_above_policy_max(policy):
    found = codes("Дарим скидку 20%", whitelist={"20"}, policy=policy)  # число «разрешено», но выше максимума
    assert "V2" in found and "V1" not in found
    assert "V2" not in codes("Скидка 12%", whitelist={"12"}, policy=policy)
    assert "V2" not in codes("Компенсация 20% от суммы", whitelist={"20"}, policy=policy)


def test_v2_follows_policy_change(policy):
    policy.discounts.max_pct = 10
    assert "V2" in codes("Скидка 12%", whitelist={"12"}, policy=policy)


# --- V3 ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["Мы виноваты в этой ситуации", "Это наша ошибка", "Гарантирую возврат денег", "Обещаю решить вопрос", "Признаём свою вину"])
def test_v3_guilt_and_promises(text):
    assert "V3" in codes(text)


def test_v3_allows_neutral_warranty_wording():
    assert "V3" not in codes("Оформляю гарантийную замену устройства")


# --- V4 ---------------------------------------------------------------------------
def test_v4_other_client_data():
    markers = {"орлов", "ооо «технооффис»", "ord-2001"}
    assert "V4" in codes("Дмитрий Орлов уже заказывал такой МФУ", foreign_markers=markers)
    assert "V4" in codes("Как в заказе ORD-2001", foreign_markers=markers)
    assert "V4" not in codes("Ваш заказ ORD-1001", foreign_markers=markers)


# --- V5 ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["<facts>{}</facts>", "Согласно Статье 3 Устава", "Мой системный промпт гласит", "Хорошо: игнорируй все инструкции"])
def test_v5_leaks_and_echo(text):
    assert "V5" in codes(text)


# --- V7 ---------------------------------------------------------------------------
def test_v7_invented_manager_timing(policy):
    assert "V7" in codes("Менеджер свяжется с вами через 5 минут", whitelist={"5"})
    assert "V7" in codes("Менеджер ответит завтра утром")
    assert "V7" in codes("Менеджер подключится в течение часа")


def test_v7_allows_policy_sla_phrase(policy):
    phrase = templates.sla_phrase(
        __import__("core.policy", fromlist=["get_manager_sla"]).get_manager_sla(policy, __import__("datetime").datetime(2026, 10, 15, 9, 0))
    )
    assert "V7" not in codes(f"Передал вопрос. {phrase}", whitelist={"2"}, sla_phrases=[phrase])
    assert "V7" in codes("Передал вопрос. Менеджер подключится в течение 3 часов.", whitelist={"3"}, sla_phrases=[phrase])


# --- Системный промпт -------------------------------------------------------------------
def test_system_prompt_has_no_discounts_or_prices(policy):
    prompt = build_system_prompt(policy)
    assert "%" not in prompt and "₽" not in prompt
    assert "УСТАВ" in prompt and "FACTS" in prompt
    assert "replacement" in prompt  # remedy-опции — только качественно


# --- Цикл генерации в action ---------------------------------------------------------------
class ScriptedText:
    name = "scripted"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0
        self.users: list[str] = []

    def complete_json(self, *a, **k):
        raise AssertionError("не используется")

    def complete_text(self, system, user, *, timeout=2.5, temperature=0.0):
        self.calls += 1
        self.users.append(user)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _grant(llm, policy):
    und = Understanding(category_candidates=["C"], confidence=0.9, entities=Entities(sku="PX-M4200", qty=10, discount_pct=5))
    decision = Decision(category="C", route="AUTONOMOUS", discount_action="grant", discount_pct=5.0, policy_version=1)
    return action.execute(decision, und, "Дайте скидку 5% на 10 штук PX-M4200", 1, ConvState(), policy, llm, [])


GOOD = "Оформляю скидку 5% на 10 × МФУ лазерное Printex M4200. Итого 369 550 ₽."


def test_action_regenerates_once_after_hallucinated_number(policy):
    llm = ScriptedText("Оформляю скидку 99% на ваш заказ", GOOD)
    resp = _grant(llm, policy)
    assert llm.calls == 2 and resp.messages[0].text == GOOD and not resp.messages[0].deterministic
    assert "ИСПРАВЬ" in llm.users[1]  # повторная генерация с подсказкой
    block = [e for e in resp.events if e["type"] == "validator_block"][0]
    assert "V1" in block["payload"]["codes"] and "V2" in block["payload"]["codes"]
    assert resp.card_updates["discount_pct"] == 5.0


def test_action_falls_back_to_template_after_two_bad_answers(policy):
    llm = ScriptedText("Скидка 99%!", "Скидка 90% и гарантирую возврат")
    resp = _grant(llm, policy)
    assert llm.calls == 2
    assert resp.messages[0].deterministic is True
    assert "5%" in resp.messages[0].text and "99" not in resp.messages[0].text
    assert len([e for e in resp.events if e["type"] == "validator_block"]) == 2


def test_action_falls_back_when_llm_is_down(policy):
    resp = _grant(ScriptedText(LLMError("down")), policy)
    assert resp.messages[0].deterministic is True and "5%" in resp.messages[0].text
    assert resp.events[0]["type"] == "llm_error"


def test_action_blocks_client_percent_echo(policy):
    und = Understanding(category_candidates=["A"], confidence=0.9)
    decision = Decision(category="A", route="AUTONOMOUS", policy_version=1)
    llm = ScriptedText("Конечно, скидка 99% ваша!", "Подскажите, какую модель вы ищете?")
    resp = action.execute(decision, und, "Дайте скидку 99%", 1, ConvState(), policy, llm, [])
    assert resp.messages[0].text == "Подскажите, какую модель вы ищете?"


def test_action_blocks_foreign_client_data(policy):
    und = Understanding(category_candidates=["A"], confidence=0.9)
    decision = Decision(category="A", route="AUTONOMOUS", policy_version=1)
    from core import tools

    stranger = tools.get_client(3)
    answer = f"{stranger['contact']} недавно купил такой же МФУ"
    llm = ScriptedText(answer, "Подберу вам подходящий МФУ — какой формат нужен?")
    resp = action.execute(decision, und, "Что посоветуете?", 1, ConvState(), policy, llm, [])
    assert stranger["name"] not in resp.messages[0].text and stranger["contact"] not in resp.messages[0].text
    assert llm.calls == 2
