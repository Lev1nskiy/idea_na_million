"""Офлайн-прогон бота: весь диалог в терминале — без Telegram, без CRM и без ключей модели.

Зачем: бота нужно показывать и проверять там, где нет ни токена от @BotFather, ни
запущенного API, ни интернета. Здесь работает та же логика, что и в Telegram
(`channels/bot_flow.py` + `channels/bot_ui.py`), только вместо транспорта Telegram —
терминал, а вместо боевой CRM — mock-CRM в памяти (`channels/mock_crm.py`).

Запуск:
    python -m channels.mock_telegram             # интерактивный диалог (mock-CRM и mock-ИИ)
    python -m channels.mock_telegram --demo      # автопрогон по всем экранам, удобно для отчёта
    python -m channels.mock_telegram --api http://localhost:8030 --bot-token <BOT_API_TOKEN>
                                                 # тот же диалог, но против живой CRM

В диалоге можно:
    • нажать кнопку по номеру (1, 2, …) или написать её подпись («👤 Профиль»);
    • написать `→nav:help` — «нажать» кнопку по её callback data;
    • написать любой текст — он уходит в чат с ИИ;
    • `менеджер <текст>` — ответить клиенту как менеджер из дашборда (проверка доставки);
    • `фид` — показать, что сейчас видит менеджер; `доставка` — забрать ответы менеджера;
    • `кнопки` — вернуть последний экран, `выход` — закончить.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from typing import Any

from channels import bot_ui as ui
from channels.bot_flow import BotError, BotSession, Event, Out
from channels.mock_crm import MockCrm
from channels.telegram_lead_bot import LeadBotConfig

LINE = "─" * 74


def _out(text: str = "") -> None:
    print(text, flush=True)


@dataclass
class TerminalBot:
    """Транспорт-терминал: рисует экраны бота и принимает ввод как клиент Telegram."""

    session: BotSession
    crm: Any
    feed: bool = True
    last_buttons: list[ui.Button] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.last_buttons = []

    # ------------------------------------------------------------------ вывод
    def show(self, out: Out, prefix: str = "🤖") -> None:
        """Печатает экран: текст, кнопки и нижнее меню."""
        self.last_buttons = []
        text = ui.plain(out.text)
        # у экранов с заголовком-разделом свой значок: второй перед текстом не нужен
        head = "" if text[:1] and not text[:1].isalnum() else f"{prefix} "
        _out(LINE)
        _out(f"{head}{text}")
        if out.screen.rows:
            _out("")
            for index, row in enumerate(out.screen.rows, start=1):
                cells = []
                for button in row:
                    self.last_buttons.append(button)
                    cells.append(f"[{len(self.last_buttons)}] {ui.plain(button.text)}")
                _out("   ".join(cells))
        if out.screen.menu:
            _out("")
            _out("⌨️  меню: " + " · ".join(ui.plain(row[0]) if len(row) == 1 else "/".join(row) for row in ui.widget_menu()))

    def show_manager_feed(self) -> None:
        """То, что в этот момент видит менеджер в дашборде (лента уведомлений)."""
        if not self.feed or not hasattr(self.crm, "take_manager_notes"):
            return
        for note in self.crm.take_manager_notes():
            _out(f"📤 в CRM (лента менеджера): {ui.plain(note)}")

    def deliver(self) -> None:
        """Забирает то, что CRM отправила клиенту: ответы менеджера и догоняющие ответы ИИ."""
        if not hasattr(self.crm, "inbox"):
            return
        for item in self.crm.inbox():
            role = item.get("role", "")
            who = {"manager": "🧑‍💼 Менеджер", "ai": "🤖 Ассистент"}.get(role, "📩 CRM")
            _out(f"📥 вам от {who}: {ui.plain(str(item.get('text', '')))}")

    # ------------------------------------------------------------------ ввод
    def resolve(self, step: str) -> Event:
        """Превращает ввод человека в событие бота: номер кнопки, подпись, callback или текст."""
        step = step.strip()
        if step.startswith("→"):
            return Event.callback(step.lstrip("→ ").strip())
        if step.isdigit():
            index = int(step) - 1
            if 0 <= index < len(self.last_buttons):
                button = self.last_buttons[index]
                _out(f"▶ нажимаю «{ui.plain(button.text)}»")
                return Event.callback(button.data or "noop")
            return Event.message(step)
        low = step.lower()
        for button in self.last_buttons:  # inline-кнопки приоритетнее: они на виду
            if low and low in button.text.lower():
                _out(f"▶ нажимаю «{ui.plain(button.text)}»")
                return Event.callback(button.data or "noop")
        for row in ui.widget_menu():  # потом подписи нижнего меню
            for label in row:
                if low and low in label.lower():
                    return Event.message(label)
        return Event.message(step)

    def send(self, step: str) -> None:
        """Прогоняет один шаг диалога и печатает результат."""
        command = step.strip().lower()
        if command in ("выход", "exit", "/exit", "quit", "/quit"):
            raise SystemExit(0)
        if command in ("кнопки", "меню"):
            return
        if command in ("фид", "feed"):
            self.show_manager_feed()
            return
        if command in ("доставка", "inbox"):
            self.deliver()
            return
        if command.startswith(("менеджер ", "manager ")) and hasattr(self.crm, "manager_reply"):
            text = step.split(" ", 1)[1]
            conversation_id = self.session.chat_id or self._last_open_appeal()
            self.crm.manager_reply(conversation_id, text)
            _out(f"🧑‍💼 менеджер написал в обращение #{conversation_id}")
            self.show_manager_feed()
            self.deliver()
            return
        if command.startswith("?"):
            _out(__doc__.split("В диалоге можно:")[1])
            return
        event = self.resolve(step)
        try:
            outs = self.session.handle(event)
        except BotError as exc:
            _out(f"⚠️ {exc}")
            return
        for out in outs:
            self.show(out)
        self.show_manager_feed()
        self.deliver()

    def _last_open_appeal(self) -> int:
        for appeal in self.crm.appeals():
            if appeal["status"] in ("ACTIVE", "HOLD", "HANDOFF"):
                return int(appeal["id"])
        raise SystemExit("нет открытого обращения — сначала создайте его")

    # ------------------------------------------------------------------ режимы работы
    def repl(self) -> int:
        """Интерактивный диалог: печатаем экран и ждём ввод (Ctrl+D — выход)."""
        _out("\nОфлайн-прогон бота: напишите номер кнопки, её подпись или текст вопроса.")
        _out("Подсказки: `менеджер <текст>` — ответ из дашборда, `фид` — лента менеджера, `выход` — конец.\n")
        for out in self.session.handle(Event.message("/start")):
            self.show(out)
        while True:
            try:
                step = input("\n👤 вы> ")
            except (EOFError, KeyboardInterrupt):
                _out("\nПока! (диалог можно повторить: python -m channels.mock_telegram --demo)")
                return 0
            if not step.strip():
                continue
            self.send(step)

    def playback(self, steps: tuple[str, ...]) -> int:
        """Автопрогон сценария: печатает шаги, чтобы демонстрацию можно было читать как протокол."""
        _out("Автопрогон диалога: меню → профиль → каталог → обращение → ИИ → менеджер → закрытие.\n")
        for step in steps:
            _out(f"\n👤 клиент> {step}")
            self.send(step)
        _out("\n" + LINE)
        _out("Автопрогон закончен. Живой диалог: python -m channels.mock_telegram")
        return 0


#: Сценарий демонстрации: проходит по всем разделам меню и обеим сторонам диалога.
DEMO_STEPS: tuple[str, ...] = (
    "/start",
    "👤 Профиль",
    "📝 Заполнить профиль",
    "Дмитрий Соколов",
    "ООО «Ромашка»",
    "+7 999 123-45-67",
    "zakupki@romashka.ru",
    "🖥 Каталог и цены",
    "Спросить про PX-M4200",
    "🆕 Обращение",
    "💰 Цена и скидка",
    "Нужно 10 МФУ PX-M4200, какой будет итог со скидкой?",
    "🆕 Обращение",
    "⚖️ Спор или брак",
    "Здравствуйте! Одно из МФУ пришло с трещиной на корпусе, нужна замена.",
    "📁 Мои обращения",
    "ждёт менеджера",
    "менеджер Добрый день! Замену согласовал, курьер приедет завтра с 10 до 14. Извините за доставленные неудобства.",
    "✅ Закрыть",
    "📁 Мои обращения",
    "❓ Помощь",
    "📚 Частые вопросы",
    "▶️",
    "⚙️ Настройки",
    "1 · выключить",
    "2 · включить",
    "💬 Менеджер",
    "/id",
    "🏠 В меню",
)


def build_terminal(api_url: str = "", bot_token: str = "", telegram_id: str = "mock-1001",
                   name: str = "Демо Клиент", feed: bool = True) -> TerminalBot:
    """Собирает транспорт: боевая CRM по адресу (`--api`) или mock-CRM в памяти."""
    if api_url and bot_token:
        config = LeadBotConfig(token="mock-transport", api_url=api_url, api_token=bot_token)
        from channels.telegram_lead_bot import BotApi

        service: Any = BotApi(config, telegram_id=telegram_id, display_name=name, username="mock")
        _out(f"Режим: живая CRM по адресу {api_url} (ответы считает её пайплайн)\n")
    else:
        service = MockCrm(telegram_id=telegram_id, display_name=name, username="mock")
        _out("Режим: mock — CRM, каталог и ответы ИИ работают локально, без интернета и токенов\n")
    session = BotSession(service, telegram_id=telegram_id, display_name=name, username="mock")
    return TerminalBot(session=session, crm=service, feed=feed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m channels.mock_telegram",
        description="Офлайн-прогон Telegram-бота: меню, профиль, обращения, чат с ИИ, вызов менеджера",
    )
    parser.add_argument("--api", default="", help="адрес CRM (например http://localhost:8030); без него работает mock-CRM")
    parser.add_argument("--bot-token", default=os.getenv("BOT_API_TOKEN", ""), help="BOT_API_TOKEN для живой CRM")
    parser.add_argument("--telegram-id", default="mock-1001", help="id клиента в CRM (в Telegram это id чата)")
    parser.add_argument("--name", default="Демо Клиент", help="имя клиента для авто-карточки")
    parser.add_argument("--demo", action="store_true", help="прогнать сценарий по всем экранам и выйти")
    parser.add_argument("--no-feed", action="store_true", help="не показывать ленту менеджера")
    args = parser.parse_args(list(argv if argv is not None else sys.argv[1:]))

    terminal = build_terminal(args.api, args.bot_token, args.telegram_id, args.name, not args.no_feed)
    return terminal.playback(DEMO_STEPS) if args.demo else terminal.repl()


if __name__ == "__main__":
    raise SystemExit(main())
