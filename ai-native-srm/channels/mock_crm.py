"""Mock-CRM: карточка клиента, обращения и ответы ИИ — в памяти процесса.

Зачем нужен mock: показать весь диалог бота (меню, профиль, обращения, чат с ИИ, вызов
менеджера) там, где нет ни токенов Telegram, ни запущенного CRM, ни модели. Это не
«заглушка-обманка», а зеркало боевого пути: те же экраны и тот же контракт
(`channels.bot_flow.BotService`), только вместо HTTP — словарь в памяти.

Ответы mock-ИИ детерминированы и собраны из тех же источников, что у настоящего пайплайна:
    • цифры — из Policy Matrix (`data/policy_seed.json`): скидки, срок ответа менеджера,
      срок замены по гарантии, компенсация за просрочку доставки;
    • факты о товарах — из каталога (`data/catalog.json`);
    • справочные ответы — из базы знаний (`data/knowledge_base.md`), тот же поиск, что в RAG.
Если факта нет — mock честно уходит в HOLD и зовёт менеджера, а не выдумывает числа.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any

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
_DISPUTE = re.compile(r"брак|сломан|не работает|претенз|вернул|возврат|обман|спор|царапин", re.IGNORECASE)
_DISCOUNT = re.compile(r"скидк|дешевл|уступ|дисконт", re.IGNORECASE)
_DELIVERY = re.compile(r"доставк|срок поставк|когда привез|курьер|задерж", re.IGNORECASE)
_WARRANTY = re.compile(r"гарант|ремонт|сервис|замен", re.IGNORECASE)
_QTY = re.compile(r"(\d{1,4})\s*(?:шт|штук\w*|единиц\w*|мфу|принтер\w*|картридж\w*)", re.IGNORECASE)


def money(value: float) -> str:
    """«38900» → «38 900» — так числа читаются в чате."""
    return f"{value:,.0f}".replace(",", " ")


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
        text = "Обращение закрыто. Понадобится помощь — просто напишите или нажмите «🆕 Обращение»."
        self._append(conversation_id, "ai", text, "FINAL")
        return text

    def call_manager(self, conversation_id: int, reason: str = "") -> str:
        row = self._appeal(conversation_id)
        if row["status"] in ("HOLD", "HANDOFF"):
            return "Менеджер уже уведомлён об этом обращении и ответит здесь, в Telegram."
        row["status"], row["status_title"], row["waiting_manager"] = "HOLD", STATUS_TITLES["HOLD"], True
        row["updated_at"] = _ts(_now())
        text = "Передал обращение менеджеру — он ответит здесь, в Telegram."
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
    def _guess_category(self, text: str) -> str:
        if _DISPUTE.search(text):
            return "спор"
        if _DISCOUNT.search(text):
            return "цена"
        if _DELIVERY.search(text):
            return "доставка"
        if _WARRANTY.search(text):
            return "гарантия"
        return "вопрос"

    def _answer(self, text: str) -> tuple[str, str, bool]:
        """(ответ клиенту, фаза, ушло ли в HOLD). Числа — только из политики и каталога."""
        facts = policy_facts()
        if _DISPUTE.search(text):
            reply = (
                "Сочувствую ситуации — так быть не должно. "
                f"Передал обращение менеджеру: по регламенту он отвечает {facts['sla_phrase']}. "
                f"Обычно вопрос решается так: {', '.join(facts['remedies'])} — согласуем с вами."
            )
            self._hold(text, reply)
            return reply, "HOLD", True
        item = self._item_in(text)
        if _DISCOUNT.search(text):
            return self._discount_answer(text, item, facts), "FINAL", False
        if item is not None:
            stock = "в наличии" if item.get("stock") else "под заказ"
            return (
                f"{item['name']} ({item['sku']}): {money(item['price'])} ₽, {stock}"
                + (f", отгрузка {item['lead_time_days']} дн." if item.get("lead_time_days") else "")
                + f", гарантия {item['warranty_months']} мес."
            ), "FINAL", False
        if _DELIVERY.search(text):
            return (
                "Отгрузка со склада — 1 рабочий день, доставка по Москве на следующий день. "
                f"Если срок будет просрочен больше чем на 2 дня, политика компании возвращает "
                f"{facts['delay_pct']}% от суммы заказа."
            ), "FINAL", False
        if _WARRANTY.search(text):
            return (
                f"Гарантия — 12 месяцев. Брак подтверждаем заменой в течение "
                f"{facts['replace_hours']} часов после осмотра (нужен номер заказа). "
                "Сервис на выезде — по обращению."
            ), "FINAL", False
        # порог выше серверного: сюда попадают только разделы, где совпал заголовок темы,
        # иначе на любой вопрос про принтер находился бы, например, раздел про 1С
        hits = rag.search(text, k=1, min_score=3.0)
        if hits:
            return _clip(hits[0].text), "FINAL", False
        # факта нет — не выдумываем число, а честно зовём человека (как решение D в пайплайне)
        reply = (
            "Не хочу называть цифры наугад: уточню у менеджера и вернусь с точным ответом. "
            f"По регламенту он отвечает {facts['sla_phrase']}."
        )
        self._hold(text, reply)
        return reply, "HOLD", True

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
            return body + " Счёт и договор оформляет менеджер — передать ему заявку?"
        tier = f" (от {money(tiers[0]['min_sum'])} ₽ — {tiers[0]['pct']}%)" if tiers else ""
        return (
            f"Скидка возможна: автономно до {facts['max_discount_pct']}%{tier}, "
            "больше — только через менеджера. Назовите модель и количество — посчитаю итог."
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

    def _item_in(self, text: str) -> dict[str, Any] | None:
        upper = text.upper()
        for item in catalog_items(limit=50):
            if item["sku"].upper() in upper:
                return item
        return None

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
        return catalog_items()

    def faq(self) -> list[tuple[str, str]]:
        return [(chunk.title, chunk.text) for chunk in rag.sections()]


def demo_created_at(days_ago: int = 0) -> str:
    """Дата для демо-обращений (mock-режим показывает «живую» историю)."""
    return _ts(_now() - timedelta(days=days_ago))
