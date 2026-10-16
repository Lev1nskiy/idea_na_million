"""Mock-CRM: карточка клиента, обращения и ответы ИИ — в памяти процесса.

Зачем нужен mock: показать весь диалог бота (меню, профиль, обращения, чат с ИИ, вызов
менеджера) там, где нет ни токенов Telegram, ни запущенного CRM, ни модели. Это не
«заглушка-обманка», а зеркало боевого пути: те же экраны и тот же контракт
(`channels.bot_flow.BotService`), только вместо HTTP — словарь в памяти.

Ответы mock-ИИ детерминированы и собраны из тех же источников, что у настоящего пайплайна:
    • цифры — из Policy Matrix (`data/policy_seed.json`): скидки, срок ответа менеджера,
      срок замены по гарантии, компенсация за просрочку доставки;
    • факты о товарах — из каталога (`data/catalog.json`): разделы техники, цены, наличие,
      сроки поставки, гарантия;
    • справочные ответы — из базы знаний (`data/knowledge_base.md`), тот же поиск, что в RAG.
Если факта нет — mock честно уходит в HOLD и зовёт менеджера, а не выдумывает числа.

Тон ответов — как у живого ассистента: обращение по имени из профиля, человеческие
формулировки («давайте посчитаем», «подскажу», «сожалею»), уточняющий вопрос в конце.
Варианты приветствий и благодарностей выбираются по тексту сообщения (`_pick`), поэтому
диалог не повторяет одну и ту же фразу, но остаётся воспроизводимым в тестах.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, Sequence

from core import rag
from core.config import DATA_DIR
from core.tools import catalog_items

#: Статусы обращений — те же строки, что в моделях CRM.
STATUS_TITLES = {
    "ACTIVE": "в работе, отвечает ИИ",
    "HOLD": "ждёт менеджера",
    "HANDOFF": "ведёт менеджер",
    "RESOLVED": "закрыто",
    "LOST": "потеряно",
}

#: Признаки спора (как в лексиконе пайплайна): сообщение уходит в HOLD, менеджер получает саммари.
_DISPUTE = re.compile(r"брак|сломан|не работает|претенз|вернул|возврат|обман|спор|царапин|трещин", re.IGNORECASE)
_DISCOUNT = re.compile(r"скидк|дешевл|уступ|дисконт", re.IGNORECASE)
_DELIVERY = re.compile(r"доставк|когда привез|курьер|задерж|привез|отгруз|самовывоз|подъём на этаж", re.IGNORECASE)
_WARRANTY = re.compile(r"гарант|ремонт|сервис|замен|обслуживан", re.IGNORECASE)
_ORDER_ID = re.compile(r"\b(?:ORD|ЗК)-\s?\d{3,5}\b", re.IGNORECASE)
_QTY = re.compile(r"(\d{1,4})\s*(?:шт|штук\w*|единиц\w*|мфу|принтер\w*|картридж\w*|ноутбук\w*|монитор\w*|пк\b)", re.IGNORECASE)
_BUDGET = re.compile(r"(?:до|бюджет|не дороже|максимум|в пределах|тысяч|тыс)\D{0,12}?(\d[\d\s]{2,})\s*(тыс|к|₽|руб)?", re.IGNORECASE)

#: Намерения «человеческого» разговора. Порядок проверки важен: сначала эмоции и явные просьбы.
_GREETING = re.compile(r"^\s*(привет|здравствуй\w*|добрый (день|вечер)|доброе утро|доброго дня|хай)\b", re.IGNORECASE)
_THANKS = re.compile(r"спасибо|благодар\w*|спс\b|отлично, спасибо", re.IGNORECASE)
_WHOAMI = re.compile(r"кто ты|ты кто|ты бот|это робот|бот\?|живой человек|что ты умеешь|что умеешь|чем помож\w*", re.IGNORECASE)
_HUMAN = re.compile(r"позов\w*|соедин\w*|дайте (мне )?менеджера|нужен менеджер|живого человека|позвон\w*|переключ\w*", re.IGNORECASE)
_DOCS = re.compile(r"счёт|счет|документ\w*|закрывающ\w*|упд|накладн\w*|оплат\w*|безнал\w*|картой|отсрочк\w*|договор\w*|реквизит\w*", re.IGNORECASE)
_INSTALL = re.compile(r"установ\w*|настро\w*|монтаж\w*|подключ\w*|инсталл\w*|обучен\w*|внедрен\w*", re.IGNORECASE)
_RENT = re.compile(r"аренд\w*|лизинг\w*|рассрочк\w*|в кредит|напрокат", re.IGNORECASE)
_UTIL = re.compile(r"утилизац\w*|списан\w*|вывез\w*|trade-?in|обменять старое|старую технику", re.IGNORECASE)
_AVAIL = re.compile(r"наличи\w*|есть ли|остал\w*|склад\w*|сроки поставки|срок поставки", re.IGNORECASE)
_PICK = re.compile(r"подбер\w*|подскаж\w*|посовет\w*|порекоменд\w*|что выбрать|какой выбрать|ищу|интересует|нужен|нужна|нужно|нужны", re.IGNORECASE)
_COMPARE = re.compile(r"сравн\w*|отлича\w*|что лучше|в чём разниц|чем различа", re.IGNORECASE)
_QUESTION = re.compile(r"какие|какой|какую|покаж\w*|посмотреть|подобрать|сколько стоят|почём|почем|цены на|есть ли", re.IGNORECASE)

#: Слова клиента → раздел каталога. Помогают ответить «нужен ноутбук и коммутатор» списком позиций.
_CATEGORY_HINTS: tuple[tuple[str, str], ...] = (
    ("laptop", r"ноутбук|ноут\b|ультрабук"),
    ("desktop", r"\bпк\b|компьютер|моноблок|рабоч\w+ станци|системн\w+ блок|тонк\w+ клиент"),
    ("monitor", r"монитор|проектор|второ\w+ экран|кронштейн"),
    ("component", r"комплектующ|процессор|оперативн|озу\b|ssd|накопител|видеокарт|материнск|корпус|кулер|апгрейд"),
    ("server", r"сервер|серверн|виртуализ"),
    ("storage", r"схд|хранилищ|nas\b|дисков\w+ полк|стример|бэкап|резервн\w+ копир"),
    ("network", r"сет[ьи]|сетево|коммутатор|свитч|маршрутизатор|роутер|wi-?fi|точк\w+ доступа|межсетев\w+ экран|vpn|патч-корд"),
    ("peripheral", r"клавиатур|мыш[ьк]|гарнитур|веб-камер|док-станц|хаб\b|штрихкод|планшет для подпис|замок"),
    ("power", r"ибп\b|бесперебойник|батаре\w*|блок розеток"),
    ("interactive", r"интерактивн|панель|вкс\b|видеоконференц|переговорн"),
    ("mfu", r"\bмфу\b"),
    ("printer", r"принтер|этикетк|финишер"),
    ("scanner", r"сканер|сканирован"),
    ("consumable", r"картридж|тонер|бумаг|фотобарабан|барабан"),
    ("office", r"шредер|ламинатор|переплётчик|резак"),
    ("software", r"лицензи|windows|office 365|антивирус|1с\b|подписк|программн\w+ обеспеч"),
    ("service", r"обслуживан|сервис|монтаж|настройк|обучени|пусконаладк"),
)

#: Человеческие формулировки. Выбор внутри группы — детерминированный (`_pick`).
GREETINGS = (
    "Здравствуйте! 👋",
    "Добрый день! 👋",
    "Привет! Рад вас видеть.",
)
THANKS_REPLIES = (
    "Всегда пожалуйста! Если появятся ещё вопросы — пишите, я на связи круглосуточно.",
    "Рад был помочь! Обращайтесь в любое время — отвечу и подберу, что нужно.",
    "Спасибо вам на добром слове! Если что-то понадобится — я здесь.",
)
SMALL_TALK = (
    "Спасибо, всё в порядке — работаю. ",
    "Всё хорошо, спасибо! ",
)


def money(value: float) -> str:
    """«38900» → «38 900» — так числа читаются в чате."""
    return f"{value:,.0f}".replace(",", " ")


def _pick(options: Sequence[str], key: str) -> str:
    """Детерминированный выбор формулировки: диалог звучит живо, но тесты воспроизводимы."""
    return options[sum(map(ord, key)) % len(options)] if options else ""


def _join_and(items: Sequence[str]) -> str:
    """«А, Б и В» — перечисление без канцелярского «и/или»."""
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " и " + items[-1]


@lru_cache(maxsize=1)
def policy_facts() -> dict[str, Any]:
    """Цифры из Policy Matrix: mock-ответы не придумывают числа, а берут их из политики."""
    seed = json.loads((DATA_DIR / "policy_seed.json").read_text(encoding="utf-8"))
    discounts = seed.get("discounts", {})
    warranty = seed.get("warranty", {})
    manager = seed.get("manager_sla", {})
    compensation = seed.get("delivery_compensation", [{}])
    hours = int(manager.get("response_hours", 1))
    return {
        "max_discount_pct": discounts.get("max_pct", 0),
        "tiers": discounts.get("tiers", []),
        "sla_hours": hours,
        "sla_phrase": "в течение часа" if hours == 1 else f"в течение {hours} ч",
        "replace_hours": warranty.get("replace_hours", 24),
        "delay_pct": compensation[-1].get("pct_of_sum", 0),
        "remedies": ["замена", "скидка", "бонус"],
    }


def _now() -> datetime:
    return datetime.now()


def _ts(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _clip(text: str, limit: int = 700) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


@dataclass
class MockCrm:
    """CRM в памяти: профиль, обращения, переписка и очередь уведомлений менеджеру."""

    telegram_id: str = "demo-client"
    display_name: str = "Демо Клиент"
    username: str = "demo"
    profile_card: dict[str, Any] | None = None
    appeal_rows: list[dict[str, Any]] = field(default_factory=list)
    messages: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    manager_notes: list[str] = field(default_factory=list)  # лента уведомлений менеджера
    outbox: list[dict[str, Any]] = field(default_factory=list)  # ответы менеджера клиенту
    _next_id: int = 1000

    # ------------------------------------------------------------------ профиль
    def profile(self) -> dict[str, Any] | None:
        return self.profile_card

    def save_profile(self, answers: dict[str, str]) -> dict[str, Any]:
        """Заводит или обновляет карточку и возвращает, что именно изменилось."""
        name = (answers.get("name") or "").strip() or self.display_name or "Клиент Telegram"
        fields = {
            "name": name,
            "company": (answers.get("company") or "").strip(),
            "phone": (answers.get("phone") or "").strip(),
            "email": (answers.get("email") or "").strip(),
        }
        created = self.profile_card is None
        if created:
            self.profile_card = {
                "id": 1,
                "status": "new",
                "created_at": _ts(_now()),
                "appeals_total": 0,
                "appeals_open": 0,
            }
            changed = list(fields)
        else:
            assert self.profile_card is not None
            changed = [key for key, value in fields.items() if self.profile_card.get(key, "") != value]
        assert self.profile_card is not None
        self.profile_card.update(fields)
        self.profile_card["appeals_total"] = len(self.appeal_rows)
        self.profile_card["appeals_open"] = sum(1 for a in self.appeal_rows if a["status"] in ("ACTIVE", "HOLD", "HANDOFF"))
        if created:
            self.manager_notes.append(
                f"🔔 Новая заявка из бота: {name} (@{self.username}) — карточка клиента создана"
            )
        elif changed:
            self.manager_notes.append(f"🔔 Клиент обновил профиль ({', '.join(changed)}): {name}")
        return {
            "ok": True,
            "created": created,
            "changed": changed,
            "client": dict(self.profile_card),
        }

    # ------------------------------------------------------------------ обращения
    def appeals(self, open_only: bool = False) -> list[dict[str, Any]]:
        """Обращения клиента, свежие сверху (как отдаёт `GET /api/bot/me/appeals`)."""
        rows = [a for a in self.appeal_rows if not open_only or a["status"] in ("ACTIVE", "HOLD", "HANDOFF")]
        return sorted(rows, key=lambda item: item["updated_at"], reverse=True)

    def appeal(self, conversation_id: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        row = self._appeal(conversation_id)
        return dict(row), list(self.messages.get(conversation_id, []))

    def _appeal(self, conversation_id: int) -> dict[str, Any]:
        for row in self.appeal_rows:
            if row["id"] == conversation_id:
                return row
        raise RuntimeError("Обращение не найдено")

    def close_appeal(self, conversation_id: int) -> str:
        row = self._appeal(conversation_id)
        row["status"], row["status_title"], row["waiting_manager"] = "RESOLVED", STATUS_TITLES["RESOLVED"], False
        row["updated_at"] = _ts(_now())
        text = "Обращение закрыто. Понадобится помощь — просто напишите, я рядом."
        self._append(conversation_id, "ai", text, "FINAL")
        return text

    def call_manager(self, conversation_id: int, reason: str = "") -> str:
        row = self._appeal(conversation_id)
        if row["status"] in ("HOLD", "HANDOFF"):
            return "Менеджер уже в курсе по этому обращению — ответит здесь, в Telegram."
        row["status"], row["status_title"], row["waiting_manager"] = "HOLD", STATUS_TITLES["HOLD"], True
        row["updated_at"] = _ts(_now())
        facts = policy_facts()
        text = f"Передал менеджеру — он ответит здесь, в Telegram, {facts['sla_phrase']}."
        self._append(conversation_id, "ai", text, "HOLD")
        self.manager_notes.append(
            f"⏳ HOLD · обращение #{conversation_id}: клиент просит менеджера. "
            f"Саммари: тема «{row['category']}», реплик {row['messages']} → /dashboard"
        )
        return text

    # ------------------------------------------------------------------ переписка
    def chat(self, text: str, conversation_id: int | None = None) -> dict[str, Any]:
        """Сообщение клиента → детерминированный ответ mock-ИИ."""
        created_client = self.profile_card is None
        if created_client:
            self.save_profile({"name": self.display_name})
        row = self._open_appeal(conversation_id, text)
        self._append(row["id"], "client", text, "FINAL")
        reply, phase, hold = self._answer(text)
        self._append(row["id"], "ai", reply, phase)
        row["updated_at"] = _ts(_now())
        row["category"] = row["category"] or ("спор" if hold else "вопрос")
        return {
            "ok": True,
            "conversation_id": row["id"],
            "created_client": created_client,
            "messages": [{"role": "client", "text": text, "phase": "FINAL"}, {"role": "ai", "text": reply, "phase": phase}],
        }

    def _open_appeal(self, conversation_id: int | None, text: str) -> dict[str, Any]:
        """Находит открытое обращение или заводит новое — как `_ensure_conversation` в пайплайне."""
        if conversation_id is not None:
            return self._appeal(conversation_id)
        for row in self.appeal_rows:
            if row["status"] in ("ACTIVE", "HOLD", "HANDOFF"):
                return row
        self._next_id += 1
        row = {
            "id": self._next_id,
            "status": "ACTIVE",
            "status_title": STATUS_TITLES["ACTIVE"],
            "category": self._guess_category(text),
            "channel": "telegram",
            "created_at": _ts(_now()),
            "updated_at": _ts(_now()),
            "last_text": "",
            "last_role": "",
            "messages": 0,
            "waiting_manager": False,
        }
        self.appeal_rows.append(row)
        self.messages[row["id"]] = []
        return row

    def _append(self, conversation_id: int, role: str, text: str, phase: str) -> None:
        self.messages.setdefault(conversation_id, []).append(
            {"id": len(self.messages.get(conversation_id, [])) + 1, "role": role, "text": text,
             "phase": phase, "ts": _ts(_now())}
        )
        row = self._appeal(conversation_id)
        row["last_text"], row["last_role"] = text, role
        row["messages"] = len(self.messages[conversation_id])

    # ------------------------------------------------------------------ ответы mock-ИИ
    def _address(self) -> str:
        """«Дмитрий, » — если клиент назвался. Пустая строка, пока имени нет."""
        name = str((self.profile_card or {}).get("name") or "").strip()
        first = name.split()[0] if name else ""
        return f"{first}, " if first and first not in ("Клиент", "Демо", "Telegram") else ""

    def _guess_category(self, text: str) -> str:
        """Тема обращения для карточки в CRM — по тем же признакам, что и ответ."""
        if _DISPUTE.search(text):
            return "спор"
        if _DISCOUNT.search(text):
            return "цена"
        if _ORDER_ID.search(text) or _DELIVERY.search(text):
            return "доставка"
        if _WARRANTY.search(text):
            return "гарантия"
        if _DOCS.search(text):
            return "документы"
        if _INSTALL.search(text):
            return "монтаж и настройка"
        if self._category_in(text):
            return "подбор"
        return "вопрос"

    def _answer(self, text: str) -> tuple[str, str, bool]:
        """(ответ клиенту, фаза, ушло ли в HOLD). Числа — только из политики и каталога."""
        facts = policy_facts()
        stripped = text.strip()

        if _DISPUTE.search(text):
            return self._reply_dispute(stripped, facts), "HOLD", True
        if _GREETING.match(stripped) and len(stripped) < 60:
            return self._reply_greeting(stripped), "FINAL", False
        if _THANKS.search(stripped) and len(stripped) < 90:
            return _pick(THANKS_REPLIES, stripped), "FINAL", False
        if _WHOAMI.search(stripped):
            return self._reply_whoami(), "FINAL", False
        if _HUMAN.search(stripped):
            reply = (
                "Конечно, зову менеджера — человек подключится к этому обращению и ответит "
                f"здесь, в Telegram, {facts['sla_phrase']}. "
                "Пока он читает переписку, можете дописать детали: всё попадёт в саммари."
            )
            self._hold(stripped, reply)
            return reply, "HOLD", True

        titles = [str(item.get("sku")) for item in catalog_items() if str(item.get("sku")).upper() in text.upper()]
        item = self._item_in(text)
        if len(titles) >= 2 and _COMPARE.search(text):
            return self._reply_compare(titles, stripped), "FINAL", False
        if _DISCOUNT.search(text):
            return self._discount_answer(text, item, facts), "FINAL", False
        if item is not None:
            return self._reply_item(item, stripped), "FINAL", False
        if _ORDER_ID.search(text):
            return self._reply_order(stripped, facts), "FINAL", False
        if _DELIVERY.search(text):
            return self._reply_delivery(facts), "FINAL", False
        if _WARRANTY.search(text):
            return self._reply_warranty(facts), "FINAL", False
        if _DOCS.search(text):
            return self._reply_section("Оплата и документы", "документы и оплату"), "FINAL", False
        if _INSTALL.search(text):
            return self._reply_section("Монтаж, настройка и обучение", "установку и настройку"), "FINAL", False
        if _RENT.search(text):
            return self._reply_section("Аренда, лизинг, рассрочка", "аренду и лизинг"), "FINAL", False
        if _UTIL.search(text):
            return self._reply_section("Утилизация и trade-in", "утилизацию и trade-in"), "FINAL", False
        # раздел каталога сам по себе (например, «корпусе принтера») — ещё не запрос подбора:
        # нужен либо явный глагол выбора, либо вопрос «какие/сколько стоят/покажите»
        if _PICK.search(text) or (_QUESTION.search(text) and self._category_in(text)):
            picked = self._reply_pick(stripped)
            if picked:
                return picked, "FINAL", False
        if _AVAIL.search(text):
            return self._reply_availability(), "FINAL", False
        # порог выше серверного: сюда попадают только разделы, где совпал заголовок темы,
        # иначе на любой вопрос про принтер находился бы, например, раздел про 1С
        hits = rag.search(text, k=1, min_score=3.0)
        if hits:
            return _clip(hits[0].text), "FINAL", False
        # факта нет — не выдумываем число, а честно зовём человека (как решение D в пайплайне)
        reply = (
            "Не хочу называть цифры наугад: уточню у менеджера и вернусь с точным ответом. "
            f"По регламенту он отвечает {facts['sla_phrase']}. "
            "Если удобно, напишите детали — приложу их к обращению."
        )
        self._hold(text, reply)
        return reply, "HOLD", True

    # --- отдельные ответы -----------------------------------------------------
    def _reply_greeting(self, text: str) -> str:
        hello = _pick(GREETINGS, text)
        name = self._address()
        if name:  # «Дмитрий, здравствуйте!» — так приветствие читается по-человечески
            hello = name + hello[0].lower() + hello[1:]
        return (
            f"{hello} {_pick(SMALL_TALK, text)}Я ИИ-ассистент компании «ЛОСЬ»: подберу технику, "
            "расскажу про цены, наличие, доставку и гарантию — а сложное передам менеджеру.\n"
            "С чего начнём: подбор, вопрос по заказу или обращение?"
        )

    def _reply_whoami(self) -> str:
        return (
            "Я ИИ-ассистент компании «ЛОСЬ», не человек 🙂 "
            "Умею подобрать технику по задаче и бюджету, посчитать скидку, рассказать про доставку, "
            "гарантию и сервис, завести обращение и показать историю.\n"
            "Если вопрос сложный или спорный — честно скажу об этом и позову менеджера."
        )

    def _reply_dispute(self, text: str, facts: dict[str, Any]) -> str:
        reply = (
            f"{self._address()}сочувствую — так быть не должно, и мне важно, чтобы вы остались довольны. "
            "Я передал обращение менеджеру вместе с перепиской: "
            f"{facts['sla_phrase']} он ответит здесь, в Telegram.\n"
            f"Обычно вопрос решается так: {_join_and(facts['remedies'])} — "
            "окончательное решение менеджер согласует с вами. Если есть номер заказа или серийный номер, "
            "пришлите — так быстрее."
        )
        self._hold(text, reply)
        return reply

    def _reply_item(self, item: dict[str, Any], text: str) -> str:
        """Ответ по конкретной позиции: цена, наличие, срок и гарантия из каталога."""
        stock = item.get("stock")
        lead = item.get("lead_time_days")
        if stock is None:
            availability = "это услуга: сроки согласуем под ваш объём работ"
        elif stock:
            availability = f"на складе {stock} шт." + (f", отгрузим за {lead} раб. дн." if lead else "")
        else:
            availability = f"сейчас под заказ, поставка {lead} дн." if lead else "сейчас под заказ"
        parts = [
            f"{self._address()}по «{item['name']}» ({item['sku']}) — {money(item['price'])} ₽.",
            availability.capitalize() + ("" if availability.endswith(".") else "."),
        ]
        if item.get("warranty_months"):
            parts.append(f"Гарантия {item['warranty_months']} мес.")
        if item.get("category_title"):
            parts.append(f"Раздел каталога: «{item['category_title']}».")
        return " ".join(parts) + " Нужно посчитать количество или скидку — скажите, посчитаю."

    def _reply_compare(self, skus: Sequence[str], text: str) -> str:
        """Сравнение двух позиций из каталога: цифры только из справочника."""
        by_sku = {str(item["sku"]).upper(): item for item in catalog_items()}
        left, right = (by_sku[sku.upper()] for sku in skus[:2])
        lines = [
            f"{self._address()}сравню по ключевому:",
            f"• {left['name']} ({left['sku']}) — {money(left['price'])} ₽, "
            f"{'в наличии' if left.get('stock') else 'под заказ'}, гарантия {left.get('warranty_months')} мес.",
            f"• {right['name']} ({right['sku']}) — {money(right['price'])} ₽, "
            f"{'в наличии' if right.get('stock') else 'под заказ'}, гарантия {right.get('warranty_months')} мес.",
        ]
        cheaper, pricier = sorted((left, right), key=lambda i: i["price"])
        lines.append(
            f"Разница в цене — {money(pricier['price'] - cheaper['price'])} ₽. "
            "Если расскажете задачу (сколько человек, какие программы), подберу точнее — "
            "или позову менеджера, он поможет с выбором."
        )
        return "\n".join(lines)

    def _reply_pick(self, text: str) -> str:
        """Подбор по разделу каталога и бюджету: «нужен ноутбук до 80 000»."""
        key = self._category_in(text)
        budget = self._budget_in(text)
        pool = catalog_items(category=key) if key else catalog_items()
        if not pool:
            return ""
        affordable = [item for item in pool if budget and item["price"] <= budget]
        chosen = (affordable or pool)[:3]
        title = chosen[0].get("category_title") or "каталог"
        lines = [f"{self._address()}посмотрел раздел «{title}» — вот что подходит:"]
        for item in chosen:
            availability = f"в наличии: {item['stock']}" if item.get("stock") else "под заказ"
            lines.append(f"• {item['name']} ({item['sku']}) — {money(item['price'])} ₽, {availability}")
        if budget and not affordable:
            cheapest = min(pool, key=lambda i: i["price"])
            lines.append(
                f"До {money(budget)} ₽ в этом разделе пока ничего нет: самый доступный вариант — "
                f"{cheapest['name']} за {money(cheapest['price'])} ₽."
            )
        elif key and len(pool) > len(chosen):
            lines.append(f"Всего в разделе {len(pool)} позиций — остальные в «🖥 Каталог и цены».")
        lines.append("Скажите количество и задачу — посчитаю итог со скидкой.")
        return "\n".join(lines)

    def _reply_availability(self) -> str:
        return (
            "Уточню с удовольствием — назовите модель или раздел техники "
            "(например, «PX-M4200» или «ноутбуки»), и я посмотрю наличие и сроки поставки по складу. "
            "Свободный текст тоже подойдёт: «нужен ноутбук до 80 000» — подберу варианты."
        )

    def _reply_order(self, text: str, facts: dict[str, Any]) -> str:
        order = _ORDER_ID.search(text)
        order_id = order.group(0).upper() if order else ""
        return (
            f"{self._address()}номер {order_id} принял. "
            "Отгружаем со склада за 1 рабочий день, а срок по конкретному заказу видит менеджер "
            "в CRM — переписал ему запрос.\n"
            f"Он ответит здесь, в Telegram, {facts['sla_phrase']}. Если задержка окажется больше двух дней, "
            f"политика компании предусматривает компенсацию {facts['delay_pct']}% от суммы заказа."
        )

    def _reply_delivery(self, facts: dict[str, Any]) -> str:
        return (
            f"{self._address()}по доставке так: со склада отгружаем за 1 рабочий день, "
            "по городу обычно привозим на следующий, крупногабарит поднимаем на этаж. "
            "Самовывоз со склада тоже возможен — предупредите заранее, подготовим.\n"
            f"Если срок сдвинется больше чем на два дня, политика компании возвращает "
            f"{facts['delay_pct']}% от суммы заказа. Нужно проверить конкретный заказ — назовите номер."
        )

    def _reply_warranty(self, facts: dict[str, Any]) -> str:
        return (
            f"{self._address()}гарантия идёт по карточке товара, обычно от года и больше. "
            "Если что-то сломалось, оформляем замену: достаточно подтверждённого заказа — "
            "по номеру заказа или серийному номеру.\n"
            f"Замена оформляется в течение {facts['replace_hours']} часов, сервис на выезде — по обращению. "
            "Расскажите, что случилось, — посмотрю, что можно сделать быстрее."
        )

    def _reply_section(self, title: str, human_topic: str) -> str:
        """Ответ из конкретного раздела базы знаний (тот же текст, что читает ИИ в CRM)."""
        for chunk in rag.sections():
            if chunk.title.startswith(title):
                return f"{self._address()}про {human_topic} — коротко:\n{_clip(chunk.text)}\nЕсли нужны детали, подключу менеджера."
        return f"{self._address()}расскажу про {human_topic}: подключу менеджера, он ответит точнее."

    def _discount_answer(self, text: str, item: dict[str, Any] | None, facts: dict[str, Any]) -> str:
        """Скидка считается кодом по Policy Matrix: ИИ называет только те цифры, что разрешены."""
        tiers = [t for t in facts["tiers"]]
        qty_match = _QTY.search(text)
        if item is not None and qty_match:
            qty = int(qty_match.group(1))
            total = item["price"] * qty
            pct = min(max((t["pct"] for t in tiers if total >= t["min_sum"]), default=0), facts["max_discount_pct"])
            body = f"{qty} × {money(item['price'])} ₽ = {money(total)} ₽."
            if pct:
                body += f" На сумму от {money(min(t['min_sum'] for t in tiers if total >= t['min_sum']))} ₽ политика даёт {pct}% → итого {money(total * (1 - pct / 100))} ₽."
            else:
                body += f" Порог скидки — от {money(tiers[0]['min_sum'])} ₽, порог пока не пройден."
            return f"{self._address()}посчитал: {body} Счёт и договор оформляет менеджер — передать ему заявку?"
        tier = f" (от {money(tiers[0]['min_sum'])} ₽ — {tiers[0]['pct']}%)" if tiers else ""
        return (
            f"{self._address()}скидка возможна: автономно до {facts['max_discount_pct']}%{tier}, "
            "больше — только через менеджера. Назовите модель и количество — посчитаю итог сразу."
        )

    def _hold(self, question: str, reply: str) -> None:
        """Уводит диалог в HOLD и ставит уведомление менеджеру — как спор в пайплайне."""
        row = self.appeal_rows[-1] if self.appeal_rows else None
        if row is None:
            return
        row["status"], row["status_title"], row["waiting_manager"] = "HOLD", STATUS_TITLES["HOLD"], True
        self.manager_notes.append(
            f"⏳ HOLD · обращение #{row['id']}: {_clip(question, 60)} → саммари готово, ждёт менеджера"
        )

    # --- разбор текста --------------------------------------------------------
    def _item_in(self, text: str) -> dict[str, Any] | None:
        upper = text.upper()
        for item in catalog_items():
            if str(item["sku"]).upper() in upper:
                return item
        return None

    def _category_in(self, text: str) -> str:
        """Раздел каталога по словам клиента («ноутбук», «коммутатор», «картридж»)."""
        for key, pattern in _CATEGORY_HINTS:
            if re.search(pattern, text, re.IGNORECASE):
                return key
        return ""

    def _budget_in(self, text: str) -> float | None:
        """Бюджет из фразы «до 80 000», «бюджет 150 тыс» → число в рублях."""
        match = _BUDGET.search(text)
        if not match:
            return None
        value = float(re.sub(r"\s", "", match.group(1)))
        if (match.group(2) or "").lower() == "тыс" or value <= 1000:
            value *= 1000  # «80 тыс» и «до 80» читаем как тысячи — так спрашивают в чате
        return value

    # ------------------------------------------------------------------ менеджер (другая сторона)
    def manager_reply(self, conversation_id: int, text: str, manager: str = "Ирина (менеджер)") -> None:
        """Эмулирует ответ менеджера из дашборда: сообщение попадёт клиенту через `inbox()`."""
        row = self._appeal(conversation_id)
        row["status"], row["status_title"], row["waiting_manager"] = "HANDOFF", STATUS_TITLES["HANDOFF"], False
        row["updated_at"] = _ts(_now())
        self._append(conversation_id, "manager", text, "FINAL")
        self.outbox.append({"conversation_id": conversation_id, "text": text, "role": "manager", "phase": "FINAL"})
        self.manager_notes.append(f"🧑‍💼 Менеджер {manager} ответил в обращении #{conversation_id}")

    def inbox(self) -> list[dict[str, Any]]:
        """Ответы менеджера, которые бот ещё не доставил клиенту (эмуляция `/api/bot/me/inbox`)."""
        items, self.outbox = list(self.outbox), []
        return items

    def take_manager_notes(self) -> list[str]:
        """То, что видит менеджер в дашборде: забираем накопившиеся уведомления."""
        notes, self.manager_notes = list(self.manager_notes), []
        return notes

    # ------------------------------------------------------------------ справочники
    def catalog(self) -> list[dict[str, Any]]:
        """Весь каталог: витрина сама раскладывает позиции по разделам."""
        return catalog_items()

    def faq(self) -> list[tuple[str, str]]:
        return [(chunk.title, chunk.text) for chunk in rag.sections()]


def demo_created_at(days_ago: int = 0) -> str:
    """Дата для демо-обращений (mock-режим показывает «живую» историю)."""
    return _ts(_now() - timedelta(days=days_ago))
