"""Экраны Telegram-бота: тексты, кнопки и меню — без aiogram и без сети.

Модуль отвечает только на вопрос «что видит клиент»: собирает текст экрана и клавиатуру
(нижний виджет-меню и inline-кнопки). Вся логика переходов — в `channels/bot_flow.py`,
транспорт Telegram — в `channels/telegram_lead_bot.py`, офлайн-прогон — в
`channels/mock_telegram.py`. Благодаря такому разделению экраны проверяются тестами и
рисуются в терминале (mock-режим), не требуя ни токена, ни интернета.

Разметка — HTML (его понимает Telegram), а `plain()` снимает теги для терминала.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# --------------------------------------------------------------------------- кнопки-константы
# Подписи нижнего меню: по ним же распознаётся нажатие (в Telegram это обычный текст).
BTN_NEW = "🆕 Обращение"
BTN_APPEALS = "📁 Мои обращения"
BTN_PROFILE = "👤 Профиль"
BTN_CHAT = "🤖 Чат с ИИ"
BTN_MANAGER = "💬 Менеджер"
BTN_HELP = "❓ Помощь"
BTN_CATALOG = "🖥 Каталог и цены"
BTN_SETTINGS = "⚙️ Настройки"

#: Нижний виджет-меню: всегда на экране, работает и когда клиент «потерялся» в диалоге.
WIDGET: tuple[tuple[str, ...], ...] = (
    (BTN_NEW, BTN_APPEALS),
    (BTN_CHAT, BTN_MANAGER),
    (BTN_PROFILE, BTN_HELP),
    (BTN_CATALOG, BTN_SETTINGS),
)

#: Темы обращения. Второй элемент — «затравка» фразы: она уходит в CRM первой строкой,
#: чтобы ИИ-пайплайн (в том числе mock) сразу понял, о чём речь.
TOPICS: tuple[tuple[str, str, str], ...] = (
    ("stock", "📦 Наличие и сроки", "Хочу уточнить наличие и сроки поставки."),
    ("price", "💰 Цена и скидка", "Хочу обсудить цену и возможную скидку."),
    ("delivery", "🚚 Доставка", "Вопрос по доставке заказа."),
    ("warranty", "🛠 Гарантия и сервис", "Вопрос по гарантии и сервисному обслуживанию."),
    ("claim", "⚖️ Спор или брак", "Есть претензия: товар пришёл с браком, нужна замена."),
    ("other", "✳️ Другое", ""),
)
TOPIC_BY_SLUG = {slug: (label, hint) for slug, label, hint in TOPICS}

#: Разделы каталога на витрине: как группировать категории из `data/catalog.json`,
#: чтобы клиент видел 6 понятных разделов, а не 17 технических.
CATALOG_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("computers", "💻 Компьютеры и мониторы", ("laptop", "desktop", "monitor")),
    ("servers", "🧩 Комплектующие, серверы, СХД", ("component", "server", "storage")),
    ("network", "🌐 Сеть, питание, ВКС", ("network", "power", "interactive")),
    ("print", "🖨 Печать и сканирование", ("mfu", "printer", "scanner")),
    ("office", "📦 Расходники и офис", ("consumable", "office")),
    ("software", "💿 ПО, сервис и услуги", ("software", "service")),
)
CATALOG_GROUP_TITLES = {key: title for key, title, _ in CATALOG_GROUPS}
CATALOG_GROUP_KEYS = {key: keys for key, _, keys in CATALOG_GROUPS}
CATALOG_PAGE = 6  #: позиций на экране раздела: больше — сообщение становится простыней

STATUS_ICON = {
    "ACTIVE": "🤖",
    "HOLD": "⏳",
    "HANDOFF": "🧑‍💼",
    "RESOLVED": "✅",
    "LOST": "⚪",
}
ROLE_LABEL = {"client": "👤 Вы", "ai": "🤖 Ассистент", "manager": "🧑‍💼 Менеджер"}


# --------------------------------------------------------------------------- структуры
@dataclass(frozen=True)
class Button:
    """Кнопка inline-клавиатуры: подпись + callback data либо внешняя ссылка."""

    text: str
    data: str = ""
    url: str = ""


@dataclass(frozen=True)
class Screen:
    """Экран бота: текст и клавиатуры.

    `rows` — inline-кнопки под сообщением (основная навигация-виджет),
    `menu=True` — показывать нижнее меню (его бот держит постоянно).
    """

    text: str
    rows: tuple[tuple[Button, ...], ...] = ()
    menu: bool = True


def rows(*groups: tuple[Button, ...]) -> tuple[tuple[Button, ...], ...]:
    return tuple(groups)


def btn(text: str, data: str = "", url: str = "") -> Button:
    return Button(text=text, data=data, url=url)


# --------------------------------------------------------------------------- утилиты
def plain(text: str) -> str:
    """Снимает HTML-теги: терминальный mock-режим и логи не должны показывать разметку."""
    out, skip = [], False
    for char in text:
        if char == "<":
            skip = True
        elif char == ">":
            skip = False
        elif not skip:
            out.append(char)
    return "".join(out)


def money(value: float | int) -> str:
    """«38900» → «38 900» — цены в чате читаются, когда разряды разделены."""
    return f"{value:,.0f}".replace(",", " ")


def clip(text: str, limit: int = 90) -> str:
    """Обрезает длинную реплику для списка, не разрывая слово посередине."""
    one_line = " ".join(text.split())
    if len(one_line) <= limit:
        return one_line
    return one_line[: limit - 1].rsplit(" ", 1)[0] + "…"


def fmt_ts(value: str | datetime | None) -> str:
    """Дата в виде «16.10 14:32» — коротко и по-русски."""
    if value is None:
        return "—"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value[:16]
    return value.strftime("%d.%m %H:%M")


def status_icon(status: str) -> str:
    return STATUS_ICON.get(status, "•")


def status_line(status: str, title: str = "") -> str:
    return f"{status_icon(status)} {title or status}"


def transcript(messages: list[dict], limit: int = 8) -> str:
    """Последние реплики диалога: клиент ↔ ИИ ↔ менеджер (для экрана обращения и чата)."""
    tail = messages[-limit:]
    if not tail:
        return "<i>Пока пусто.</i>"
    return "\n\n".join(
        f"{ROLE_LABEL.get(msg.get('role', ''), '•')}: {msg.get('text', '')}" for msg in tail
    )


def tech_note(msg: dict) -> str:
    """Техническая сноска про ответ ИИ: фаза (FINAL/INTERIM/HOLD) и шаблон или модель."""
    phase = msg.get("phase") or "FINAL"
    kind = "шаблон без модели" if phase in ("HOLD", "ACK") else "ответ пайплайна"
    return f"<i>тех. детали: фаза {phase}, {kind}</i>"


# --------------------------------------------------------------------------- меню
def widget_menu() -> tuple[tuple[str, ...], ...]:
    """Нижнее меню (в Telegram — reply-клавиатура, в mock-режиме — список команд)."""
    return WIDGET


def main_screen(name: str | None, appeals_open: int, appeals_total: int) -> Screen:
    """Главный экран: кто ты, сколько обращений и виджет-меню из пяти разделов."""
    if name:
        who = f"👤 <b>{name}</b> · 📁 обращений: {appeals_total} ({appeals_open} в работе)"
    else:
        who = "👤 <b>Профиль не заполнен</b> — нажмите «👤 Профиль», чтобы назваться."
    text = (
        "🤖 <b>AI-native CRM</b> · бот компании «ЛОСЬ»\n\n"
        "Я отвечаю на вопросы про товары, цены, доставку, гарантию и ремонт, а сложное "
        "передаю менеджеру. Вся переписка попадает в CRM — история не теряется.\n\n"
        f"{who}\n\n"
        "Выберите раздел — или просто напишите вопрос, и я отвечу."
    )
    return Screen(
        text,
        rows(
            (btn("👤 Профиль", "nav:profile"), btn("📁 Обращения", "nav:appeals")),
            (btn("🆕 Новое обращение", "nav:new"),),
            (btn("🤖 Чат с ИИ", "nav:chat"), btn("💬 Позвать менеджера", "nav:manager")),
            (btn("❓ Помощь и FAQ", "nav:help"), btn("🖥 Каталог", "nav:catalog"), btn("⚙️ Настройки", "nav:settings")),
        ),
    )


def profile_screen(card: dict) -> Screen:
    """Карточка клиента: контакты, статус и статистика обращений."""
    text = (
        "👤 <b>Профиль</b>\n\n"
        f"Имя: <b>{card.get('name', '—')}</b>\n"
        f"Компания: {card.get('company') or '—'}\n"
        f"Телефон: {card.get('phone') or '—'}\n"
        f"E-mail: {card.get('email') or '—'}\n"
        f"В CRM с: {fmt_ts(card.get('created_at'))}\n"
        f"Обращений: {card.get('appeals_total', 0)} (в работе {card.get('appeals_open', 0)})\n\n"
        "Карточку видит менеджер — по ней он звонит и пишет."
    )
    return Screen(text, rows((btn("✏️ Изменить", "prof:edit"), btn("📁 Обращения", "nav:appeals")), (btn("🏠 В меню", "nav:menu"),)))


def profile_missing_screen() -> Screen:
    """Клиента ещё нет в CRM: объясняем, зачем заполнять профиль."""
    return Screen(
        "👤 <b>Профиль</b>\n\n"
        "Вас пока нет в CRM: как только вы назовётесь, менеджер сможет позвонить или "
        "написать вам по делу. Имя обязательно, телефон и e-mail — по желанию.\n\n"
        "Заполнить сейчас?",
        rows((btn("📝 Заполнить профиль", "prof:fill"),), (btn("🏠 В меню", "nav:menu"),)),
    )


def question_screen(question: str, progress: str) -> Screen:
    """Шаг анкеты (профиль или новое обращение) — текст вопроса и кнопка отмены."""
    return Screen(
        f"{question}\n\n<i>{progress}</i>",
        rows((btn("✖️ Отменить", "nav:menu"),)),
    )


def profile_saved_screen(card: dict, changed: list[str]) -> Screen:
    """Что изменилось после сохранения профиля."""
    what = ", ".join(changed) if changed else "ничего не менялось"
    return Screen(
        "✅ <b>Профиль сохранён</b>\n\n"
        f"Обновлено: {what}. Менеджер увидит изменения в карточке клиента.\n\n"
        + profile_screen(card).text,
        rows((btn("📁 Обращения", "nav:appeals"), btn("🤖 Чат с ИИ", "nav:chat")), (btn("🏠 В меню", "nav:menu"),)),
    )


def topics_screen() -> Screen:
    """Первый шаг нового обращения: тема (кнопки вместо свободного текста)."""
    return Screen(
        "🆕 <b>Новое обращение</b>\n\n"
        "О чём обращение? Выберите тему — я подскажу её менеджеру и быстрее подберу ответ.",
        rows(*[(btn(label, f"ap:topic:{slug}"),) for slug, label, _ in TOPICS], (btn("✖️ Отменить", "nav:menu"),)),
    )


def appeal_text_screen(topic_label: str) -> Screen:
    """Второй шаг: свободное описание. Пишем, что уйдёт в CRM."""
    return Screen(
        f"🆕 <b>{topic_label}</b>\n\n"
        "Опишите ситуацию своими словами: модель или артикул, количество, сроки — что важно.\n\n"
        "<i>Сообщение уйдёт в CRM, ответит ИИ (а при необходимости подключится менеджер).</i>",
        rows((btn("✖️ Отменить", "nav:menu"),)),
    )


def appeals_screen(appeals: list[dict]) -> Screen:
    """Список обращений: статус, тема и последняя реплика; открыть — кнопкой."""
    if not appeals:
        return Screen(
            "📁 <b>Мои обращения</b>\n\n"
            "Обращений пока нет. Начните с «🆕 Обращение» — я отвечу сразу, а история "
            "останется в CRM.",
            rows((btn("🆕 Новое обращение", "nav:new"),), (btn("🏠 В меню", "nav:menu"),)),
        )
    lines: list[str] = []
    buttons: list[tuple[Button, ...]] = []
    for item in appeals[:10]:
        lines.append(
            f"{status_icon(item['status'])} <b>#{item['id']}</b> · {item.get('status_title', item['status'])}\n"
            f"   {clip(item.get('last_text') or '—', 80)}\n"
            f"   <i>{fmt_ts(item.get('updated_at'))} · сообщений: {item.get('messages', 0)}</i>"
        )
        buttons.append((btn(f"#{item['id']} · {item.get('status_title', item['status'])}", f"ap:open:{item['id']}"),))
    return Screen(
        "📁 <b>Мои обращения</b>\n\n" + "\n\n".join(lines),
        rows(*buttons, (btn("🆕 Новое обращение", "nav:new"), btn("🏠 В меню", "nav:menu"))),
    )


def appeal_screen(appeal: dict, messages: list[dict], techie: bool = False) -> Screen:
    """Обращение: переписка, статус и действия (ответ, менеджер, закрыть)."""
    head = (
        f"{status_icon(appeal['status'])} <b>Обращение #{appeal['id']}</b> · "
        f"{appeal.get('status_title', appeal['status'])}\n"
        f"<i>открыто {fmt_ts(appeal.get('created_at'))}"
        + (f" · тема: {appeal['category']}" if appeal.get("category") else "")
        + "</i>\n\n"
    )
    body = transcript(messages)
    if techie and messages:
        body += "\n\n" + tech_note(messages[-1])
    actions: list[tuple[Button, ...]] = []
    if appeal["status"] in ("ACTIVE", "HOLD", "HANDOFF"):
        actions.append((btn("✍️ Ответить", f"ap:reply:{appeal['id']}"), btn("✅ Закрыть", f"ap:close:{appeal['id']}")))
        if appeal["status"] == "ACTIVE":
            actions.append((btn("💬 Позвать менеджера", f"ap:mgr:{appeal['id']}"),))
        else:
            actions.append((btn("🔔 Менеджер уже уведомлён", "noop"),))
    else:
        actions.append((btn("🆕 Новое обращение", "nav:new"),))
    actions.append((btn("📁 Все обращения", "nav:appeals"), btn("🏠 В меню", "nav:menu")))
    return Screen(head + body, rows(*actions))


def chat_screen(messages: list[dict], techie: bool = False, conversation_id: int | None = None) -> Screen:
    """Ответ ИИ в режиме чата: реплики + действия."""
    body = transcript(messages, limit=6)
    if messages and messages[-1].get("phase") == "HOLD":
        # пайплайн сам перевёл диалог на менеджера — клиент не должен этого ждать молча
        body += "\n\n⏳ <i>Обращение передано менеджеру: он ответит здесь, в Telegram.</i>"
    if techie and messages:
        body += "\n\n" + tech_note(messages[-1])
    tail_actions: list[tuple[Button, ...]] = [
        (btn("💬 Позвать менеджера", f"ap:mgr:{conversation_id}" if conversation_id else "nav:manager"),
         btn("📁 Мои обращения", "nav:appeals")),
        (btn("🏠 В меню", "nav:menu"),),
    ]
    return Screen(body, rows(*tail_actions))


def manager_screen(appeals: list[dict]) -> Screen:
    """Позвать менеджера: по открытым обращениям или объяснение, если их нет."""
    open_items = [a for a in appeals if a["status"] in ("ACTIVE", "HOLD", "HANDOFF")]
    if not open_items:
        return Screen(
            "💬 <b>Менеджер</b>\n\n"
            "Открытых обращений нет — менеджеру пока не о чем вас спрашивать. Создайте "
            "обращение: если ИИ не справится или вы попросите, менеджер подключится сам.",
            rows((btn("🆕 Новое обращение", "nav:new"),), (btn("🏠 В меню", "nav:menu"),)),
        )
    text = (
        "💬 <b>Позвать менеджера</b>\n\n"
        "Он увидит переписку, саммари по обращению и ваши контакты. ИИ в этом обращении "
        "замолчит и не будет мешать.\n\n"
        "По какому обращению позвать?"
    )
    buttons = [
        (btn(f"#{item['id']} · {item.get('status_title', item['status'])}", f"ap:mgr:{item['id']}"),)
        for item in open_items[:8]
    ]
    return Screen(text, rows(*buttons, (btn("🏠 В меню", "nav:menu"),)))


def manager_called_screen(text: str) -> Screen:
    """Подтверждение вызова менеджера."""
    return Screen(
        f"✅ {text}\n\n"
        "Пока ждёте — можно закрыть тему или открыть новое обращение: этот запрос менеджер уже видит.",
        rows((btn("📁 Мои обращения", "nav:appeals"),), (btn("🏠 В меню", "nav:menu"),)),
    )


def appeal_closed_screen(text: str) -> Screen:
    """Подтверждение закрытия обращения."""
    return Screen(
        f"✅ {text}",
        rows((btn("🆕 Новое обращение", "nav:new"),), (btn("🏠 В меню", "nav:menu"),)),
    )


def help_screen(has_profile: bool) -> Screen:
    """Помощь: что умеет бот, команды, частые вопросы."""
    commands = (
        "/start — меню\n"
        "/profile — профиль\n"
        "/appeals — мои обращения\n"
        "/chat — чат с ИИ\n"
        "/manager — позвать менеджера\n"
        "/id — мой номер в Telegram (для менеджера)\n"
        "/cancel — прервать заполнение"
    )
    hint = "" if has_profile else "\n\n⚠️ Профиль не заполнен — начните с «👤 Профиль»: так менеджер сможет ответить."
    text = (
        "❓ <b>Помощь</b>\n\n"
        "Я умею: принять обращение (наличие, цены, доставка, гарантия, спор), отвечать на "
        "вопросы сам, показывать историю обращений и звать менеджера.\n\n"
        f"<b>Команды</b>\n{commands}{hint}\n\n"
        "Частые вопросы — в базе знаний компании:"
    )
    return Screen(
        text,
        rows(
            (btn("📚 Частые вопросы", "faq:0"), btn("🖥 Каталог и цены", "nav:catalog")),
            (btn("🧑‍💼 Позвать менеджера", "nav:manager"),),
            (btn("🏠 В меню", "nav:menu"),),
        ),
    )


def faq_screen(sections: list[tuple[str, str]], index: int) -> Screen:
    """Раздел базы знаний (она же — источник ответов ИИ) + навигация по разделам."""
    title, body = sections[index]
    prev_i, next_i = (index - 1) % len(sections), (index + 1) % len(sections)
    nav_row = (
        btn(f"◀️ {clip(sections[prev_i][0], 18)}", f"faq:{prev_i}"),
        btn(f"{clip(sections[next_i][0], 18)} ▶️", f"faq:{next_i}"),
    )
    return Screen(
        f"📚 <b>{title}</b>\n\n{body}\n\n<i>Раздел {index + 1} из {len(sections)} базы знаний</i>",
        rows(nav_row, (btn("💬 Спросить у ИИ", "nav:chat"), btn("🧑‍💼 Менеджер", "nav:manager")), (btn("🏠 В меню", "nav:menu"),)),
    )


def catalog_screen(items: list[dict]) -> Screen:
    """Каталог и цены: обзор по разделам техники, а не сплошной список.

    Справочник вырос до сотни позиций, поэтому вход в каталог — это разделы
    («Ноутбуки», «Серверы», «Сетевое оборудование»…), а не первые попавшиеся строки.
    """
    counts: dict[str, int] = {}
    titles: dict[str, str] = {}
    for item in items:
        key = str(item.get("category") or "other")
        titles.setdefault(key, str(item.get("category_title") or "Прочее"))
        counts[key] = counts.get(key, 0) + 1

    lines = [
        "🖥 <b>Каталог и цены</b>\n",
        "Наличие и цены — из справочника CRM, по нему же отвечает ИИ.",
        f"Всего позиций: <b>{len(items)}</b> в {len(counts)} разделах.\n",
    ]
    buttons: list[tuple[Button, ...]] = []
    for group_key, group_title, keys in CATALOG_GROUPS:
        selected = [key for key in keys if counts.get(key)]
        if not selected:
            continue
        total = sum(counts[key] for key in selected)
        titles_line = ", ".join(f"{titles[key]} ({counts[key]})" for key in selected)
        lines.append(f"• <b>{group_title}</b> — {total}\n   <i>{titles_line}</i>")
        buttons.append((btn(f"{group_title} · {total}", f"cat:{group_key}:0"),))
    leftovers = [key for key in counts if key not in {k for _, _, keys in CATALOG_GROUPS for k in keys}]
    if leftovers:
        total = sum(counts[key] for key in leftovers)
        lines.append(f"• <b>Прочее</b> — {total}\n   <i>{', '.join(titles[key] for key in leftovers)}</i>")
        buttons.append((btn(f"Прочее · {total}", "cat:other:0"),))

    lines.append("\nНужна скидка или счёт — открывайте обращение: этим занимается менеджер.")
    return Screen("\n".join(lines), rows(*buttons, (btn("🆕 Обращение", "nav:new"), btn("🏠 В меню", "nav:menu"))))


def catalog_group_screen(group_title: str, items: list[dict], page: int = 0) -> Screen:
    """Позиции раздела: по `CATALOG_PAGE` штук на экран, листалка и кнопка «спросить» у каждой."""
    per_page = CATALOG_PAGE
    pages = max(1, -(-len(items) // per_page))
    page = page % pages
    window = items[page * per_page : (page + 1) * per_page]

    lines = [f"🖥 <b>{group_title}</b>\n"]
    buttons: list[tuple[Button, ...]] = []
    if not window:
        lines.append("<i>В этом разделе пока нет позиций.</i>")
    for item in window:
        stock = item.get("stock")
        warranty = item.get("warranty_months")
        details = []
        if stock is None:
            details.append("услуга")
        elif stock:
            details.append(f"в наличии: {stock}")
        else:
            details.append("под заказ")
        if warranty:
            details.append(f"гарантия {warranty} мес.")
        lines.append(f"• <b>{item['name']}</b> ({item['sku']})\n   {money(item['price'])} ₽ · {', '.join(details)}")
        buttons.append((btn(f"Спросить про {item['sku']}", f"ap:ask:{item['sku']}"),))

    if pages > 1:
        lines.append(f"\n<i>Страница {page + 1} из {pages}</i>")
        buttons.append(
            (btn("◀️ Назад", f"cat_page:{page - 1}"), btn("Ещё ▶️", f"cat_page:{page + 1}"))
        )
    buttons.append((btn("🆕 Обращение", "nav:new"), btn("◀️ Разделы", "nav:catalog")))
    buttons.append((btn("🧑‍💼 Менеджер", "nav:manager"), btn("🏠 В меню", "nav:menu")))
    return Screen("\n".join(lines), rows(*buttons))


def settings_screen(notify: bool, techie: bool) -> Screen:
    """Настройки бота: что присылать и насколько подробно показывать ответы."""
    def flag(value: bool) -> str:
        return "включено ✅" if value else "выключено ⛔"

    return Screen(
        "⚙️ <b>Настройки</b>\n\n"
        f"1. Уведомления об ответах менеджера: <b>{flag(notify)}</b>\n"
        "   <i>сообщения менеджера из CRM приходят в этот чат сразу</i>\n\n"
        f"2. Технические детали ответов ИИ: <b>{flag(techie)}</b>\n"
        "   <i>фаза ответа (FINAL/INTERIM/HOLD) и признак шаблона — для демонстрации</i>",
        rows(
            (btn(f"1 · {'выключить' if notify else 'включить'}", "set:notify"),),
            (btn(f"2 · {'выключить' if techie else 'включить'}", "set:techie"),),
            (btn("🏠 В меню", "nav:menu"),),
        ),
    )


def error_screen(text: str) -> Screen:
    """Понятная ошибка вместо технических деталей."""
    return Screen(f"⚠️ {text}", rows((btn("🏠 В меню", "nav:menu"),)))


def unknown_screen() -> Screen:
    """Не поняли ввод — возвращаем к меню, не теряя контекст."""
    return Screen(
        "🤔 Не понял сообщение. Напишите вопрос обычными словами или выберите раздел меню.",
        rows((btn("👤 Профиль", "nav:profile"), btn("📁 Обращения", "nav:appeals")), (btn("🏠 В меню", "nav:menu"))),
    )
