"""Приёмка канала «бот-анкета → CRM → сайт»: одна команда вместо ручной проверки.

Что делает: проходит тот же путь, что и бот, и на каждом шаге говорит, где порвалось.

    1. API жив?                       GET  /api/health
    2. Ручка заявок принимает данные? POST /api/leads   (заголовок X-Bot-Token)
    3. Анкета стала клиентом?         GET  /api/leads   (последние заявки)
    4. Клиент и уведомление видны сайту?  GET /api/clients, /api/notifications   (если указан --site)

Телеграм для проверки не нужен: анкета отправляется напрямую в API — тем же телом, что шлёт бот.

Запуск (PowerShell):
    python scripts\\check_lead_pipeline.py --api http://localhost:8000 --site http://localhost:3000

Токены берутся из .env, если не переданы ключами: --bot-token, --login, --password.
Скрипт создаёт в базе тестовую карточку «Проверка канала» — её видно в списке клиентов,
удалить можно демо-сбросом («Сбросить демо-данные» в сайдбаре).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE_DIR = Path(__file__).resolve().parent.parent


def env_file_value(name: str) -> str:
    """Читает значение из .env без зависимостей проекта (скрипт — приёмка окружения)."""
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


def setting(name: str) -> str:
    """Приоритет: переменная окружения → .env (как это делает core.config)."""
    return os.getenv(name) or env_file_value(name)

TEST_CLIENT = "Проверка канала"
TEST_PHONE = "+79990000000"
TIMEOUT_S = 15.0


class Step:
    """Результат одной проверки: печатаем сразу, чтобы было видно, где остановились."""

    def __init__(self) -> None:
        self.failed = 0

    def ok(self, title: str, detail: str = "") -> None:
        print(f"  [ok]   {title}" + (f" — {detail}" if detail else ""))

    def fail(self, title: str, hint: str) -> None:
        self.failed += 1
        print(f"  [СТОП] {title}")
        print(f"         что делать: {hint}")

    def warn(self, title: str, hint: str = "") -> None:
        print(f"  [--]   {title}" + (f" — {hint}" if hint else ""))


def call(url: str, *, method: str = "GET", body: dict | None = None, token: str = "", header: str = "X-Bot-Token") -> tuple[int, dict]:
    """Простой HTTP-вызов на стандартной библиотеке: скрипт работает без зависимостей."""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers[header] = token
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            raw = response.read().decode("utf-8", "replace")
            return response.status, (json.loads(raw) if raw.strip().startswith(("{", "[")) else {"raw": raw})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"detail": raw[:300]}
        return exc.code, payload
    except urllib.error.URLError as exc:
        return 0, {"detail": f"{exc.reason}"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка канала «Telegram-бот-анкета → CRM → сайт»")
    parser.add_argument("--api", default=os.getenv("API_URL", "http://localhost:8000"), help="адрес API")
    parser.add_argument("--site", default=os.getenv("SITE_URL", ""), help="адрес сайта (проверка витрины, необязательно)")
    parser.add_argument("--bot-token", default=setting("BOT_API_TOKEN"), help="BOT_API_TOKEN (по умолчанию из .env)")
    parser.add_argument("--login", default=os.getenv("ADMIN_LOGIN", "admin"), help="логин администратора сайта")
    parser.add_argument("--password", default=os.getenv("ADMIN_PASSWORD", "admin"), help="пароль администратора")
    args = parser.parse_args()

    api = args.api.rstrip("/")
    step = Step()
    print("Проверка канала «бот-анкета → CRM → сайт»")
    print(f"  API: {api}")
    print(f"  сайт: {args.site or 'не проверяем (не передан --site)'}\n")

    # 1. API -----------------------------------------------------------------------------------
    print("1. API отвечает")
    status, health = call(f"{api}/api/health")
    if status != 200:
        step.fail(f"GET {api}/api/health → {status or 'нет ответа'} ({health.get('detail')})",
                  "запустите бэкенд: python -m uvicorn app.main:app --host 0.0.0.0 --port 8000")
        return 1
    step.ok("health", f"статус {health.get('status')}, провайдер {health.get('provider')}, политика v{health.get('policy_version')}")

    if not args.bot_token:
        step.fail("BOT_API_TOKEN не задан",
                  "добавьте BOT_API_TOKEN в .env бэкенда (этим ключом защищена ручка заявок) и перезапустите API")
        return 1

    # 2. Ручка заявок --------------------------------------------------------------------------
    print("\n2. Ручка заявок принимает анкету (как её шлёт бот)")
    payload = {
        "name": TEST_CLIENT,
        "company": "Проверка канала",
        "phone": "8 999 000-00-00",
        "email": "check@pipeline.local",
        "request": "техническая проверка канала бот → CRM",
        "telegram_id": "000000",
        "telegram_username": "pipeline_check",
        "source": "pipeline_check",
    }
    status, report = call(f"{api}/api/leads", method="POST", body=payload, token=args.bot_token)
    if status == 401:
        step.fail("POST /api/leads → 401 неверный X-Bot-Token",
                  "BOT_API_TOKEN в .env бэкенда и в окружении бота должны совпадать; после правки перезапустите оба")
        return 1
    if status == 503:
        step.fail("POST /api/leads → 503 приём заявок выключен",
                  "в .env бэкенда не задан BOT_API_TOKEN — впишите и перезапустите API")
        return 1
    if status != 200:
        step.fail(f"POST /api/leads → {status}: {report}",
                  "проверьте тело запроса и логи API (uvicorn печатает трейсбек)")
        return 1
    client_id = report.get("client_id")
    step.ok("анкета принята", f"карточка #{client_id}, {'создана' if report.get('created') else 'обновлена'}"
                              + (f", изменилось: {report.get('changed')}" if report.get("changed") else ""))
    step.ok("уведомление менеджеру", str(report.get("notification"))[:110])
    if TEST_PHONE.lstrip("+") in str(report.get("notification")) or "check@pipeline.local" in str(report.get("notification")):
        step.fail("в уведомлении оказались контакты клиента",
                  "ПДн не должны покидать карточку: проверьте текст уведомления в core/leads.py")
    else:
        step.ok("ПДн в уведомлении нет", "телефон и почта остались только в карточке клиента")

    # 3. Заявка стала клиентом ------------------------------------------------------------------
    print("\n3. Анкета видна в списке заявок API")
    status, leads = call(f"{api}/api/leads?limit=10", token=args.bot_token)
    if status != 200:
        step.fail(f"GET /api/leads → {status}", "ручка требует тот же X-Bot-Token")
    elif any(item.get("client_id") == client_id for item in leads.get("leads", [])):
        step.ok("заявка в журнале", f"всего последних: {leads.get('total')}")
    else:
        step.fail("созданной заявки нет в журнале", "проверьте логи API: событие lead_received должно записаться")

    # 4. Витрина сайта ---------------------------------------------------------------------------
    if not args.site:
        print("\n4. Витрина сайта — пропущено (передайте --site http://localhost:3000)")
    else:
        site = args.site.rstrip("/")
        print(f"\n4. Сайт показывает карточку и уведомление ({site})")
        status, auth = call(f"{site}/api/auth/login", method="POST",
                            body={"login": args.login, "password": args.password})
        if status != 200 or not auth.get("token"):
            step.fail(f"вход на сайт → {status}", "проверьте логин/пароль (--login/--password), демо: admin/admin")
        else:
            step.ok("вход", f"роль {(auth.get('user') or {}).get('role')}")
            token = auth["token"]
            status, clients = call(f"{site}/api/clients", token=token, header="X-Los-Token")
            names = [c.get("name") for c in clients.get("clients", [])] if status == 200 else []
            if any(TEST_CLIENT in (name or "") for name in names):
                step.ok("карточка клиента видна сайту", f"всего клиентов: {len(names)}")
            else:
                step.fail(f"карточки «{TEST_CLIENT}» нет в списке сайта",
                          "проверьте, что сайт смотрит на тот же бэкенд (CRM_BACKEND_URL) и тот же файл БД")
            status, feed = call(f"{site}/api/notifications", token=token, header="X-Los-Token")
            lead_notes = [n for n in feed.get("notifications", []) if n.get("type") == "LEAD"]
            if status == 200 and lead_notes:
                step.ok("уведомление «Заявка из бота» в дашборде", f"тип LEAD, всего таких: {len(lead_notes)}")
            else:
                step.fail("в ленте уведомлений нет заявок типа LEAD",
                          "откройте дашборд менеджера и проверьте /api/notifications в браузере")

    print()
    if step.failed:
        print(f"Канал не готов: проверок не прошло — {step.failed}. Смотрите подсказки выше.")
        return 1
    print("Канал работает: бот → API → карточка клиента → сайт. Можно запускать сам бот:")
    print("  python -m channels.telegram_lead_bot --check   # затем без --check")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
