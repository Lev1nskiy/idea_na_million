# API и UI (шаги 5–6)

Запуск двух процессов (или `docker-compose` после Шага 6):

```bash
cd ai-native-srm
pip install -r requirements.txt
cp .env.example .env                     # LLM_PROVIDER=mock — демо без сети
python scripts/seed_demo.py              # клиенты + политика версии 1

uvicorn app.main:app --host 0.0.0.0 --port 8000        # API + /docs
streamlit run ui/Home.py --server.port 8501            # UI (страницы 1–4 в меню слева)
```

Собранный фронтенд можно отдать тем же приложением: задайте `FRONTEND_DIR` (каталог со `index.html`) —
сайт будет доступен на `/`, API на `/api`, Swagger на `/docs`. Подробности и готовый TS-клиент:
`docs/frontend_integration.md`, `frontend-kit/`.

`ADMIN_TOKEN` в `.env` включает админ-ручки переноса данных; он же нужен UI
(`ADMIN_TOKEN=demo-token streamlit run ui/Home.py`), иначе раздел «Перенос данных» покажет подсказку.

## Эндпоинты

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/health` | живость, LLM-провайдер, версия политики |
| POST | `/api/messages` | сообщение клиента → `BotResponse` (ACK/F мгновенно, INTERIM по таймеру 3.5 с) |
| GET | `/api/conversations/{id}/messages?after=` | поллинг сообщений диалога |
| GET | `/api/outbox?channel=` | батч недоставленных сообщений для канала (Telegram, Шаг 6) |
| GET | `/api/conversations?status=` | список диалогов (ACTIVE / HOLD / HANDOFF / RESOLVED / LOST) |
| GET | `/api/conversations/{id}` | карточка: клиент, сделка, саммари Handoff, решения, уведомления |
| POST | `/api/conversations/{id}/takeover` | менеджер подключается → HANDOFF, напоминания стоп |
| POST | `/api/conversations/{id}/manager-reply` | ответ менеджера (в messages + outbox) |
| POST | `/api/conversations/{id}/resolve` | закрыть диалог |
| POST | `/api/decisions/{id}/override` | override решения (кто, почему, когда) |
| GET | `/api/decisions` | журнал решений (категория, маршрут, версия политики) |
| GET | `/api/clients`, `/api/clients/{id}` | выбор клиента в UI (без телефонов/почты, Ст. 4); `telegram_id` — только с `X-Admin-Token` |
| GET | `/api/clients/by-telegram/{telegram_id}` | резолв клиента для Telegram-бота: `{client_id}` или 404 (ПДн не отдаются) |
| GET | `/api/deals`, `/api/deals/{id}`, PUT `/api/deals/{id}` | карточки сделок |
| GET | `/api/policy`, PUT `/api/policy`, GET `/api/policy/history` | Policy Management (каждая правка — новая версия) |
| GET | `/api/notifications`, POST `/api/notifications/{id}/read`, POST `/api/notifications/read-all` | уведомления менеджеру |
| GET | `/api/metrics` | 7 метрик ТЗ + TTFR раздельно + override_rate + recall |
| GET | `/api/security/incidents` | инциденты injection (только маскированный текст, Ст. 4) |
| GET/POST | `/api/admin/export`, `/api/admin/import`, `/api/admin/backup`, PUT `/api/admin/reference-data/{name}` | перенос данных (заголовок `X-Admin-Token`) |

## Страницы UI

| Страница | Что показывает |
|---|---|
| `Home.py` | состояние стенда (провайдер, версия политики) и сценарий демо |
| `1_Client_Chat.py` | симулятор клиента + 6 быстрых кнопок (FAQ, Скидка, Гарантия, Спор, Injection, Лимит скидки) |
| `2_Manager_Dashboard.py` | очередь ACTIVE/HOLD/HANDOFF, уведомления с «ждёт N мин», саммари сверху, takeover, ответ, override, resolve |
| `3_Policy_Admin.py` | форма политики, история версий, дампы состояния |
| `4_Security_Metrics.py` | 7 метрик, инциденты (маска), журнал решений с override |

UI не содержит бизнес-логики: только вызовы `ui/api_client.py` (обёртка над REST).

## Сквозной сценарий для жюри (5 минут)

1. `1_Client_Chat` → «FAQ» → ответ из каталога с ценой и наличием.
2. «Скидка» → скидка 5% оформлена, карточка сделки заполнена.
3. «Спор» → мгновенный ACK (< 100 мс), диалог в HOLD, уведомление менеджеру.
4. `2_Manager_Dashboard` → саммари спора, «Взять диалог», ответ клиенту, override.
5. `3_Policy_Admin` → максимальная скидка 15% → 10%, сохранить.
6. `1_Client_Chat` → повторить «Скидка» или «Лимит скидки» → ответ уже по новой политике (без перезапуска).
7. «Injection» → скрипт F без LLM + инцидент; `4_Security_Metrics` → метрики и лог инцидентов.
