# Перенос данных, бэкап и восстановление (шаги 5–6)

Четыре разные задачи, которые обычно путают под словом «бэкап»:

| Что переносим | Чем | Кому нужно |
|---|---|---|
| **БД целиком** (восстановление 1:1) | `scripts/db_backup.py` — бинарный снимок SQLite | деплой, аварийное восстановление |
| **Состояние БД** (диалоги, решения, сделки, политика) | `core/portability.py`, `scripts/export_state.py`, `scripts/import_state.py` | перенос демо между машинами, аудит заказчику |
| **Обезличенный срез** (метрики без ПДн) | тот же экспорт с `--mask` | передача наружу и аналитика (Ст. 4) |
| **Справочники `data/*`** (каталог, клиенты, КБ, шаблоны КП) | `PUT /api/admin/reference-data/{name}` | загрузка данных заказчика без правки репозитория |

Справочники **не** попадают в дампы БД: они лежат файлами в `data/` и переносятся вместе с кодом.

---

## Команды

```bash
# обезличенный срез (можно отдавать наружу)
python scripts/export_state.py --out dumps/state_masked.json --mask

# полный дамп состояния (содержит ПДн — только закрытый контур)
python scripts/export_state.py --out dumps/state.json

# проверка перед переносом: ничего не меняет, показывает отчёт
python scripts/import_state.py --in dumps/state.json --dry-run

# перенос (merge по id, идемпотентно) и перенос вместе с политикой
python scripts/import_state.py --in dumps/state.json
python scripts/import_state.py --in dumps/state.json --policy

# бинарный бэкап живой БД + ротация 7 снимков
python scripts/db_backup.py --out backups/crm-$(date +%F).db --rotate 7

# восстановление (сервис обязательно остановить)
python scripts/db_backup.py --in backups/crm-2026-10-15.db
```

По HTTP (нужен заголовок `X-Admin-Token`):

```bash
curl -H "X-Admin-Token: $ADMIN_TOKEN" -o state.json "http://localhost:8000/api/admin/export?mask=false"
curl -H "X-Admin-Token: $ADMIN_TOKEN" -F "file=@state.json" "http://localhost:8000/api/admin/import?dry_run=true"
curl -H "X-Admin-Token: $ADMIN_TOKEN" -o crm.sqlite3 "http://localhost:8000/api/admin/backup"
curl -H "X-Admin-Token: $ADMIN_TOKEN" -F "file=@catalog.json" -X PUT \
     "http://localhost:8000/api/admin/reference-data/catalog"
```

---

## Правила, которые нельзя нарушать

1. **`cp crm.db` — не бэкап.** При активных записях копия может быть битой (журнал в файл не попадает). Используйте `Connection.backup()` — он и вызывается внутри скрипта.
2. **Восстановление — только на остановленном сервисе**: подмена файла + удаление `-journal/-wal/-shm`. Поэтому эндпоинта `POST /restore` нет сознательно.
3. **История политик не перезаписывается никогда.** Строки `policy_versions` не импортируются построчно: иначе версия с тем же номером затрёт чужую и поедет аудит решений (Ст. 2). Политика из дампа применяется как **новая** версия через `update_policy()`.
4. **Полный дамп — чувствительный артефакт**: внутри полный текст переписки. Наружу — только `--mask`.
5. **Обезличенный дамп нельзя импортировать** (скрипт и API отвечают понятной ошибкой), иначе «пустая» база восстановится молча.
6. **Импорт атомарный**: любая ошибка валидации → откат целиком + отчёт по строкам (ошибки без текста клиента).
7. **Версия схемы проверяется** (`schema_version != 1` → отказ). Полноценные миграции — после хакатона (Alembic).
8. **Порядок таблиц — родители раньше детей** (важно при переезде на Postgres, где FK включены всегда).
9. **Деплой:** смонтировать volume/persistent disk под файл БД, иначе на Render/Railway база умрёт при рестарте. Cron: `python scripts/db_backup.py --out backups/crm-$(date +%F).db --rotate 7`.

---

## Что в обезличенном дампе остаётся

Вырезаются: `clients.name/phone/email/company`, `messages.text`, `deals.notes`, `decisions.action_summary`,
`notifications.text`, `outbox.text`, `security_incidents.raw_text_masked`, `events.payload`, `handoffs.summary_json`.

Остаются: `id`, роли, фазы, категории, статусы, `confidence`, `latency_ms`, `ts`, `pattern`, `text_hash` —
по ним считаются все метрики, но прочитать переписку нельзя. У `decisions.reasons` от слов клиента
остаются только коды («pre_classifier:claim»), у инцидентов — хэш и имя паттерна.

---

## Встраивание в роадмап

| Шаг | Что добавить |
|---|---|
| **Шаг 5** | `app/routers/admin.py` + `include_router` в `app/main.py`; кнопки дампа на странице `3_Policy_Admin`; `ADMIN_TOKEN` в `.env`; `python-multipart` в `requirements.txt`. |
| **Шаг 6** | volume под БД + cron-бэкап в `Dockerfile`/`docker-compose.yml`; раздел «Бэкап и перенос данных» в `README.md`; в `tests/test_scenarios.py` — сценарий «смена политики → экспорт → импорт в чистую БД». |

Проверено на текущем коде: roundtrip 50 строк без расхождений, повторный импорт не дублирует,
обезличенный дамп без ПДн, восстановление после порчи файла, тесты `tests/test_portability.py`
и `tests/test_admin_api.py` (см. `pytest -q`).
