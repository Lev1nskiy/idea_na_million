"""Консольный чат с полным циклом: pre-classifier -> Understanding -> Decision -> Action.

Запуск из корня проекта:
    python scripts/chat_cli.py --client 2                  # интерактивно, клиент №2 (см. clients_history.json)
    python scripts/chat_cli.py --client 4 --once "Когда будет доставка?"
    python scripts/chat_cli.py --debug --reset             # + диагностика слоёв, чистая БД

Диалог и счётчики (скидка, уверенность) хранятся в БД между сообщениями. Пока не заданы
ключи в .env, работает MockLLM (LLM_PROVIDER=mock).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import SQLModel, col, select  # noqa: E402

from app import db as dbm  # noqa: E402
from app import models as m  # noqa: E402
from core import ai_engine  # noqa: E402
from core.config import BASE_DIR  # noqa: E402
from core.guardrails import detect_injection  # noqa: E402
from core.llm import get_llm_provider  # noqa: E402
from core.perception import pre_classify  # noqa: E402
from core.policy import get_policy  # noqa: E402
from core.schemas import IncomingMessage  # noqa: E402
from core.understanding import analyze  # noqa: E402


def _after(model, last_id: int, **filters):
    with dbm.session_scope() as s:
        stmt = select(model).where(col(model.id) > last_id).order_by(col(model.id))
        for key, value in filters.items():
            stmt = stmt.where(getattr(model, key) == value)
        return list(s.exec(stmt).all())


def _debug_layers(text: str, llm) -> None:
    t0 = time.perf_counter()
    injection = detect_injection(text)
    pre = pre_classify(text)
    ms = (time.perf_counter() - t0) * 1000
    print(f"  [debug] injection={injection.flag} {injection.patterns}; pre_classify ({ms:.1f} ms): {pre.model_dump_json()}")
    target = pre.legit_text if pre.risk == "F" else text
    if target and pre.risk != "I":
        t0 = time.perf_counter()
        und = analyze(target, [], llm)
        print(f"  [debug] understanding ({int((time.perf_counter() - t0) * 1000)} ms): {json.dumps(und.model_dump(), ensure_ascii=False)}")


async def turn(text: str, client_id: int, conv_id: int | None, llm, state: dict[str, int], debug: bool) -> int | None:
    if debug:
        _debug_layers(text, llm)
    response = await ai_engine.process_message(
        IncomingMessage(client_id=client_id, conversation_id=conv_id, text=text), llm=llm
    )
    await ai_engine.drain_background()  # дождаться «поздних» ответов после INTERIM
    conv_id = response.conversation_id

    for msg in _after(m.Message, state["msg"], conversation_id=conv_id, role="ai"):
        kind = "шаблон" if msg.deterministic else "LLM"
        print(f"бот [{msg.phase}, {kind}, {msg.latency_ms} мс]> {msg.text}")
        state["msg"] = msg.id or state["msg"]
    for d in _after(m.Decision, state["dec"], conversation_id=conv_id):
        print(f"  решение: категория={d.category} маршрут={d.route} политика v{d.policy_version} причины={d.reasons}")
        state["dec"] = d.id or state["dec"]
    for n in _after(m.Notification, state["note"], conversation_id=conv_id):
        print(f"  уведомление менеджеру: {n.type} — {n.text}")
        state["note"] = n.id or state["note"]
    with dbm.session_scope() as s:
        conv = s.get(m.Conversation, conv_id)
        print(f"  диалог #{conv_id}: статус={conv.status} категория={conv.category} suspicious={conv.suspicious}")
    return conv_id


async def amain(args: argparse.Namespace) -> None:
    dbm.configure_engine(f"sqlite:///{Path(args.db).resolve().as_posix()}")
    if args.reset:
        from app import models as _  # noqa: F401

        SQLModel.metadata.drop_all(dbm.get_engine())
    dbm.init_db()
    policy = get_policy()
    llm = get_llm_provider()
    print(f"LLM: {llm.name} | политика v{policy.version}: max скидка {policy.discounts.max_pct:g}% | клиент #{args.client}")

    state = {"msg": 0, "dec": 0, "note": 0}
    with dbm.session_scope() as s:  # не печатать историю прошлых сессий
        state["msg"] = max((r.id or 0 for r in s.exec(select(m.Message)).all()), default=0)
        state["dec"] = max((r.id or 0 for r in s.exec(select(m.Decision)).all()), default=0)
        state["note"] = max((r.id or 0 for r in s.exec(select(m.Notification)).all()), default=0)

    conv_id: int | None = None
    if args.once:
        await turn(args.once, args.client, conv_id, llm, state, args.debug)
        return
    print("Пустая строка или Ctrl+C — выход.")
    while True:
        try:
            text = (await asyncio.to_thread(input, "\nклиент> ")).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            break
        conv_id = await turn(text, args.client, conv_id, llm, state, args.debug)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    parser = argparse.ArgumentParser(description="Консольный чат с ИИ-движком")
    parser.add_argument("--client", type=int, default=1, help="client_id из data/clients_history.json (1–6)")
    parser.add_argument("--once", help="обработать одну фразу и выйти")
    parser.add_argument("--debug", action="store_true", help="показывать pre-classifier и Understanding")
    parser.add_argument("--reset", action="store_true", help="очистить БД перед запуском")
    parser.add_argument("--db", default=str(BASE_DIR / "crm_cli.db"), help="файл SQLite для CLI")
    asyncio.run(amain(parser.parse_args()))


if __name__ == "__main__":
    main()
