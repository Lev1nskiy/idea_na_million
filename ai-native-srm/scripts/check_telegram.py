"""Диагностика Telegram-канала: почему бот не получает сообщения и не отдаёт данные сайту.

Одна команда вместо гадания — проверяет по порядку всё, что стоит между клиентом в Telegram
и карточкой в CRM:

    1. токен бота в .env (и не занят ли он вторым ботом)
    2. кто этот бот в Telegram (getMe) — тот ли бот, в который пишут клиенты
    3. webhook: если установлен, long polling работать не будет
    4. кто читает апдейты прямо сейчас (конфликт из-за второго экземпляра)
    5. ждут ли апдейты обработки — то есть доходят ли сообщения до бота
    6. принял бы API заявку (проверка канала в CRM, без записи)

Запуск:
    python scripts\\check_telegram.py
    python scripts\\check_telegram.py --api http://localhost:8000

Скрипт ничего не меняет и не удаляет: он только смотрит. Готовые команды для починки печатает.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
TIMEOUT_S = 15.0

FIX_HINT = """\
Как починить (Windows, PowerShell):
  # 1. найти все запущенные экземпляры бота
  Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*telegram_lead_bot*" } |
    Select-Object ProcessId, CommandLine | Format-List
  # 2. убить лишние (uvicorn и сайт не пострадают)
  Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*telegram_lead_bot*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
  # 3. снять webhook, если он был
  python -m channels.telegram_lead_bot --drop-webhook
  # 4. запустить бота заново — только в одном окне
  python -m channels.telegram_lead_bot"""


class Report:
    def __init__(self) -> None:
        self.problems: list[str] = []

    def ok(self, text: str) -> None:
        print(f"  [ok]   {text}")

    def fail(self, text: str, advice: str = "") -> None:
        self.problems.append(text)
        print(f"  [СТОП] {text}")
        if advice:
            print("         " + advice.replace("\n", "\n         "))


def env_value(name: str) -> str:
    """Читает значение из .env без зависимостей проекта."""
    env = os.getenv(name)
    if env:
        return env.strip()
    path = BASE_DIR / ".env"
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == name:
            return value.strip().strip('"').strip("'")
    return ""


def telegram(token: str, method: str, params: dict | None = None) -> dict:
    """Вызов Telegram Bot API. Ошибки возвращаем данными, а не исключением."""
    url = f"https://api.telegram.org/bot{token}/{method}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=TIMEOUT_S) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"ok": False, "description": f"HTTP {exc.code}: {raw[:200]}"}
    except urllib.error.URLError as exc:
        return {"ok": False, "description": f"нет связи с api.telegram.org: {exc.reason}"}


def api_call(url: str, *, token: str = "", method: str = "GET", body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Bot-Token"] = token
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            raw = response.read().decode("utf-8", "replace")
            return response.status, (json.loads(raw) if raw.strip().startswith(("{", "[")) else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"detail": raw[:200]}
    except urllib.error.URLError as exc:
        return 0, {"detail": str(exc.reason)}


def conflict_reason(probe: dict) -> str | None:
    """Почему getUpdates не отдал апдейты: чужой экземпляр, webhook или ошибка токена.

    Вынесено отдельно: это то самое место, где «данные из телеграма не приходят» превращается
    в понятную причину, поэтому оно проверяется тестами без сети.
    """
    if probe.get("ok"):
        return None
    description = str(probe.get("description", ""))
    # webhook тоже даёт 409, поэтому его распознаём раньше «чужого экземпляра»:
    # лечение у этих случаев разное (снять webhook против убить лишний процесс)
    if "webhook" in description.lower():
        return "установлен webhook: Telegram не отдаёт апдейты в long polling"
    if probe.get("error_code") == 409 or "terminated by other getUpdates" in description:
        return "конфликт: бота уже опрашивает другой экземпляр"
    if probe.get("error_code") == 401 or "unauthorized" in description.lower():
        return "токен не принят Telegram"
    return f"getUpdates не сработал: {description or 'без описания'}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Диагностика Telegram-бота-анкеты")
    parser.add_argument("--api", default=env_value("API_URL") or "http://localhost:8000", help="адрес API")
    args = parser.parse_args()

    api = args.api.rstrip("/")
    report = Report()
    print("Диагностика Telegram-канала «бот-анкета → CRM»")
    print(f"  папка проекта: {BASE_DIR}")
    print(f"  API:           {api}\n")

    # 1. токены -------------------------------------------------------------------------------
    print("1. Настройки")
    token = env_value("TG_LEAD_BOT_TOKEN")
    bot_api_token = env_value("BOT_API_TOKEN")
    chat_bot_token = env_value("TG_BOT_TOKEN")

    if not token:
        report.fail("TG_LEAD_BOT_TOKEN не задан в .env",
                    "получите токен у @BotFather (/newbot) и впишите в ai-native-srm/.env")
        return 1
    report.ok("TG_LEAD_BOT_TOKEN задан")
    if not bot_api_token:
        report.fail("BOT_API_TOKEN не задан — сервер ответит 503 и заявка не дойдёт",
                    "впишите один и тот же ключ в .env бота и бэкенда: python -c \"import secrets;print(secrets.token_urlsafe(32))\"")
    else:
        report.ok("BOT_API_TOKEN задан")

    # Частая ошибка: один токен на двух разных ботов — оба опрашивают Telegram и мешают друг другу
    if chat_bot_token and chat_bot_token == token:
        report.fail("TG_BOT_TOKEN и TG_LEAD_BOT_TOKEN совпадают — на одном токене два бота",
                    "это гарантированный TelegramConflictError: создайте в @BotFather ОТДЕЛЬНОГО бота "
                    "для анкеты и положите его токен в TG_LEAD_BOT_TOKEN")
    elif chat_bot_token:
        report.ok("токены клиентского бота и бота-анкеты разные")

    # 2. кто это в Telegram -------------------------------------------------------------------
    print("\n2. Бот в Telegram")
    me = telegram(token, "getMe")
    if not me.get("ok"):
        report.fail(f"токен не принят Telegram: {me.get('description')}",
                    "проверьте токен: он мог быть отозван (@BotFather → /mybots → API Token)")
        return 1
    info = me.get("result") or {}
    username = info.get("username", "?")
    bot_id = info.get("id")
    report.ok(f"это @{username} (id {bot_id})")
    print(f"         пишите клиентам именно в @{username} — сообщения в другом боте сюда не попадут")

    # 3. webhook ------------------------------------------------------------------------------
    print("\n3. Webhook")
    webhook = (telegram(token, "getWebhookInfo").get("result") or {})
    if webhook.get("url"):
        report.fail(f"установлен webhook: {webhook['url']} — Telegram не отдаёт апдейты в getUpdates",
                    "python -m channels.telegram_lead_bot --drop-webhook")
    else:
        report.ok("webhook не установлен (long polling разрешён)")
    pending = webhook.get("pending_update_count")
    if pending:
        print(f"         в очереди Telegram: {pending} апдейт(ов) — их никто не забирал")
    if webhook.get("last_error_message"):
        print(f"         последняя ошибка доставки: {webhook['last_error_message']}")

    # 4. не читает ли бота кто-то ещё ----------------------------------------------------------
    print("\n4. Кто читает апдейты")
    probe = telegram(token, "getUpdates", {"offset": "-1", "limit": "1", "timeout": "0"})
    reason = conflict_reason(probe)
    if reason:
        report.fail(reason, FIX_HINT)
    else:
        report.ok("токен свободен — никто другой бота не опрашивает")
        # 5. доходят ли сообщения -----------------------------------------------------------
        print("\n5. Сообщения от клиентов")
        updates = probe.get("result") or []
        if updates:
            last = updates[-1]
            message = last.get("message") or {}
            sender = message.get("from") or {}
            kind = "команда" if str(message.get("text", "")).startswith("/") else "сообщение"
            report.ok(f"Telegram держит необработанное {kind} от {sender.get('first_name', '?')} "
                      f"(id {sender.get('id', '?')}) — значит апдейты до бота доходят")
            print("         это сообщение сюда не пропало: как только бот запустится, он его обработает")
        else:
            report.ok("необработанных сообщений нет — можно писать боту")

    # 6. примет ли API ------------------------------------------------------------------------
    print("\n6. Приёмная сторона (CRM)")
    status, health = api_call(f"{api}/api/health")
    if status != 200:
        report.fail(f"API не отвечает ({status or 'нет ответа'})",
                    "запустите бэкенд: python -m uvicorn app.main:app --host 0.0.0.0 --port 8000")
    else:
        report.ok(f"API жив, провайдер {health.get('provider')}")
        status, leads = api_call(f"{api}/api/leads?limit=1", token=bot_api_token)
        if status == 200:
            report.ok("ручка заявок принимает сервисный токен — канал в CRM открыт")
        elif status == 401:
            report.fail("API ответил 401: BOT_API_TOKEN бота и бэкенда не совпадают",
                        "впишите одно значение в оба .env и перезапустите API и бота")
        elif status == 503:
            report.fail("API ответил 503: на бэкенде не задан BOT_API_TOKEN",
                        "впишите ключ в .env бэкенда и перезапустите API")
        else:
            report.fail(f"GET /api/leads → {status}: {leads.get('detail')}", "смотрите логи uvicorn")

    print()
    if report.problems:
        print(f"Найдено проблем: {len(report.problems)}. Почините по подсказкам выше и запустите проверку снова.")
        return 1
    print("Всё готово: бот настроен, апдейты доходят, API принимает заявки.")
    print("Запуск бота: python -m channels.telegram_lead_bot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
