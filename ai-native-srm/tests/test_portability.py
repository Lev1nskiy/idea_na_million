"""Перенос состояния БД: roundtrip, идемпотентность, обезличивание (Ст. 4), бэкап SQLite.

Проверяем то, что нельзя проверить «на глаз»: что дамп восстанавливается в чистую БД,
что повторный импорт не дублирует строки, что обезличенный дамп не содержит Переписки
и что восстановление из бинарного снимка возвращает данные.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from sqlmodel import select

from app import models as m
from core import portability
from core.policy import get_policy, update_policy

ROOT = Path(__file__).resolve().parent.parent


def _seed(db, *, policy_max_pct: int = 10) -> None:
    """Минимальный набор: клиент, диалог, сообщения, сделка, уведомление, инцидент, событие."""
    with db.session_scope() as s:
        s.add(m.Client(id=1, name="Елена Попова", phone="+7 999 123-45-67", email="elena@example.com", status="loyal"))
        s.add(m.Conversation(id=1, client_id=1, channel="web", status="HOLD", category="I"))
        s.add(m.Message(id=1, conversation_id=1, role="client", text="Верните деньги, иначе суд! Серийник SN-ABC123456"))
        s.add(m.Message(id=2, conversation_id=1, role="ai", text="Передал менеджеру.", phase="ACK", deterministic=True, latency_ms=20))
        s.add(m.Deal(id=1, client_id=1, conversation_id=1, product="МФУ лазерное Printex M4200", qty=10, stage="negotiation"))
        s.add(m.Notification(id=1, conversation_id=1, type="DISPUTE", text="Сложный диалог: клиент ждёт менеджера"))
        s.add(m.SecurityIncident(id=1, conversation_id=1, raw_text_masked="[SERIAL]", text_hash="abc", pattern="ignore_ru"))
        s.add(m.Event(id=1, type="decision", conversation_id=1, payload={"category": "I"}))
    update_policy({"discounts": {"max_pct": policy_max_pct}}, author="admin", note="демо")


def _fresh_db(db, tmp_path: Path, name: str = "dst.db") -> None:
    """Переключает engine на пустую БД (имитация «другой машины»)."""
    db.configure_engine(f"sqlite:///{(tmp_path / name).as_posix()}")
    db.init_db()


def _counts(db) -> dict[str, int]:
    out: dict[str, int] = {}
    with db.session_scope() as s:
        for model in portability.EXPORT_ORDER:
            out[model.__tablename__] = len(s.exec(select(model)).all())
    return out


def _without_policy(counts: dict[str, int]) -> dict[str, int]:
    return {name: value for name, value in counts.items() if name != "policy_versions"}


def test_export_import_roundtrip(db, tmp_path):
    """Дамп, снятый в одной БД, восстанавливается в чистую без потерь строк."""
    _seed(db)
    dump = portability.export_state()
    _fresh_db(db, tmp_path)

    report = portability.import_state(dump)

    assert report.committed is True and report.errors == []
    assert _without_policy(_counts(db)) == _without_policy(dump["counts"])
    with db.session_scope() as s:  # содержимое, а не только количество
        assert s.get(m.Message, 1).text.startswith("Верните деньги")
        assert s.get(m.Conversation, 1).status == "HOLD"
    assert get_policy().version == 1  # политику переносили отдельным флагом


def test_policy_from_dump_becomes_new_version(db, tmp_path):
    """Политика из дампа применяется НОВОЙ версией, история версий не перезаписывается."""
    _seed(db)  # версия 2: max_pct 10
    dump = portability.export_state()
    assert dump["counts"]["policy_versions"] == 2
    _fresh_db(db, tmp_path)

    report = portability.import_state(dump, policy=True)

    assert report.skipped["policy_versions"] == 2 and report.policy_version == 2
    assert get_policy().discounts.max_pct == 10
    with db.session_scope() as s:
        versions = [r.version for r in s.exec(select(m.PolicyVersion).order_by(m.PolicyVersion.version)).all()]
    assert versions == [1, 2]  # версия 1 (seed) + версия 2 из дампа


def test_second_import_does_not_duplicate(db, tmp_path):
    """Импорт идемпотентен: повторный импорт того же дампа не плодит строки."""
    _seed(db)
    dump = portability.export_state()
    _fresh_db(db, tmp_path)

    portability.import_state(dump)
    before = _counts(db)
    report = portability.import_state(dump)

    assert report.committed is True
    assert _counts(db) == before


def test_import_is_atomic_on_invalid_row(db, tmp_path):
    """Битая строка → откат целиком: в БД не появляется ни одной строки."""
    _seed(db)
    dump = portability.export_state()
    dump["tables"]["messages"][0]["role"] = None  # role обязателен
    _fresh_db(db, tmp_path)

    report = portability.import_state(dump)

    assert report.committed is False and report.errors
    assert sum(_counts(db).values()) == 0


def test_dry_run_changes_nothing(db, tmp_path):
    """dry_run проходит валидацию, но ничего не фиксирует."""
    _seed(db)
    dump = portability.export_state()
    _fresh_db(db, tmp_path)

    report = portability.import_state(dump, policy=True, dry_run=True)

    assert report.dry_run is True and report.committed is False
    assert sum(_counts(db).values()) == 0


def test_masked_dump_hides_client_text_and_cannot_be_imported(db, tmp_path):
    """Обезличенный дамп: тексты диалога и ПДн вырезаны, структура и метрики сохранены (Ст. 4)."""
    _seed(db)

    dump = portability.export_state(mask=True)
    blob = json.dumps(dump, ensure_ascii=False)

    assert "Верните деньги" not in blob and "SN-ABC123456" not in blob
    assert "Елена Попова" not in blob and "+7 999 123-45-67" not in blob and "elena@example.com" not in blob
    assert dump["tables"]["messages"][0]["text"] == portability.REDACTED
    assert dump["tables"]["messages"][1]["latency_ms"] == 20  # метрики остались
    assert dump["tables"]["conversations"][0]["status"] == "HOLD"
    with pytest.raises(ValueError):
        portability.import_state(dump)


def test_rejects_incompatible_schema_version(db):
    with pytest.raises(ValueError):
        portability.import_state({"schema_version": 99, "tables": {}})


def test_sqlite_backup_and_restore(db, tmp_path, monkeypatch):
    """Снимок делается на живой БД; восстановление возвращает потерянные строки."""
    module = _load_db_backup()
    live = tmp_path / "live.db"
    monkeypatch.setenv("DB_URL", f"sqlite:///{live.as_posix()}")
    db.configure_engine(f"sqlite:///{live.as_posix()}")
    db.init_db()
    _seed(db)

    snapshot = module.backup(tmp_path / "backups" / "crm.db")
    assert snapshot.exists()

    con = sqlite3.connect(live)  # имитируем потерю данных
    con.execute("delete from messages")
    con.commit()
    con.close()
    assert _counts(db)["messages"] == 0

    module.restore(snapshot)
    db.dispose_engine()  # в бою на этом шаге сервис перезапускается: файл подменён
    db.init_db()

    assert _counts(db)["messages"] == 2
    assert live.with_name(live.name + ".pre-restore").exists()  # прежняя база сохранена


def test_backup_stamp_and_rotation_keep_newest(db, tmp_path, monkeypatch):
    """Регулярные бэкапы: `--stamp` даёт уникальные имена, ротация оставляет N свежих."""
    module = _load_db_backup()
    live = tmp_path / "live.db"
    monkeypatch.setenv("DB_URL", f"sqlite:///{live.as_posix()}")
    db.configure_engine(f"sqlite:///{live.as_posix()}")
    db.init_db()
    _seed(db)

    folder = tmp_path / "backups"
    for hour in range(9):
        snapshot = module.stamped(folder / "crm.db", datetime(2026, 10, 15, hour, 0, 0))
        module.backup(snapshot)

    assert len(list(folder.glob("*.db"))) == 9
    kept = module.rotate(str(folder / "*.db"), 7)

    assert len(kept) == 7
    assert kept[0].name == "crm-20261015-080000.db"      # самый свежий
    assert not (folder / "crm-20261015-000000.db").exists()
    assert module.stamped(Path("backups/crm.db"), datetime(2026, 1, 2, 3, 4, 5)).name == "crm-20260102-030405.db"


def _load_db_backup():
    """Скрипт лежит в scripts/ (не пакет) — грузим его по пути."""
    path = ROOT / "scripts" / "db_backup.py"
    spec = importlib.util.spec_from_file_location("db_backup_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
