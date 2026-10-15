# Подключение сайта коллег к этому бэкенду

Документ отвечает на один вопрос: **как соединить готовый сайт (или любой фронтенд) с этим API**.
Ничего переписывать в проекте не нужно — соединение идёт по HTTP.

Формат: `ai-native-srm/frontend-kit/openapi.json` (машинный контракт) + `ai-native-srm/frontend-kit/api-client.ts`
(готовый клиент без зависимостей). Эта страница — человеческое описание того же.

---

## 1. Три способа соединить — выберите один

| Способ | Когда подходит | Что делать |
|---|---|---|
| **A. Один origin** (рекомендуется) | сайт собран статикой (`npm run build`) | положить сборку в `ai-native-srm/frontend/`, задать `FRONTEND_DIR=./frontend` — FastAPI отдаст и сайт, и API на одном порту. Ни CORS, ни прокси, один URL для жюри |
| **B. Разные origin** | у сайта свой dev-сервер / свой хостинг | в клиенте указать `API_URL` (например `http://localhost:8000`), на стороне сайта ничего настраивать не нужно: **CORS уже открыт** (`allow_origins=["*"]`) |
| **C. Dev-proxy** | `npm run dev` на 5173, API на 8000 | в `vite.config.ts`: `server.proxy = { "/api": "http://localhost:8000" }`, в коде обращаться по относительным путям — тогда CORS вообще не участвует |

### Вариант A на практике

```powershell
cd C:\...\idea_na_million\ai-native-srm

# 1. сборка сайта (в репозитории сайта)
npm run build                      # получится dist/ или build/

# 2. положить сборку в проект
xcopy /E /I "C:\путь\к\сайту\dist" frontend

# 3. .env
FRONTEND_DIR=./frontend

# 4. запуск
python -m uvicorn app.main:app --port 8000
#    http://localhost:8000/          → сайт
#    http://localhost:8000/api/...   → API
#    http://localhost:8000/docs      → Swagger
```

В Docker тот же результат даёт `docker compose up --build` — `./frontend` монтируется автоматически
(см. `docker-compose.yml`), отдельная переменная не нужна.

Если сборки нет — `FRONTEND_DIR` пуст, и API работает как раньше. **Ничего не ломается.**

---

## 2. Что нужно знать про API (шпаргалка для коллеги)

* **База:** все пути начинаются с `/api`. Версии (`/api/v1`) пока нет — при изменениях контракта
  договоримся деплоить фронт и бэк вместе (при способе A это автоматически).
* **Формат:** JSON, UTF-8, даты — ISO-8601 без таймзоны (локальное время сервера).
* **Реальное время:** WebSocket/SSE **нет**. Используется поллинг: сообщения диалога —
  `GET /api/conversations/{id}/messages?after=<id последнего>` (1–2 с), список диалогов —
  `GET /api/conversations` (2–5 с), уведомления — `GET /api/notifications?unread_only=true` (5 с).
* **Аутентификации у клиентских ручек нет** (`POST /api/messages`, чтение диалогов открыты) —
  для закрытого контура это ок, для публичного нужен общий слой авторизации; админ-ручки
  (`/api/admin/*`) требуют заголовок `X-Admin-Token`.
* **Ошибки:** FastAPI-стандарт — `{"detail": "текст"}` + HTTP-код (404 — нет объекта, 422 — схема, 503 — админ-ручка без токена).
* **Ключевые формы ответов** (проверены на живом стенде):

```jsonc
// POST /api/messages  →  ответ ИИ приходит синхронно
{
  "conversation_id": 3,
  "messages": [ { "text": "Нашёл подходящие варианты: …", "phase": "FINAL", "deterministic": false } ],
  "card_updates": [], "notifications": [], "events": []
}
// phase: ACK (спор, отвечено мгновенно) | INTERIM (3.5 с, «принял, уточняю») | FINAL | HOLD

// GET /api/conversations?status=HOLD&limit=50
{ "id": 3, "client_id": 1, "client_name": "Алексей Смирнов", "channel": "web", "status": "HOLD",
  "category": "I", "suspicious": false, "assigned_manager": null, "unread_notifications": 1,
  "last_text": "…", "last_role": "client", "last_ts": "…", "updated_at": "…" }

// GET /api/conversations/3
{ "conversation": { … }, "client": {…}, "deal": {…}, "handoff": {…}, "decisions": [ … ], "notifications": [ … ] }

// GET /api/conversations/3/messages?after=0
{ "id": 12, "role": "ai", "text": "…", "phase": "FINAL", "deterministic": false, "latency_ms": 412, "ts": "…" }

// GET /api/decisions — журнал решений (для админ-панели и метрик)
{ "id": 8, "conversation_id": 3, "category": "B", "route": "AUTONOMOUS", "confidence": 0.9,
  "policy_version": 1, "reasons": ["category:B"], "action_summary": "…",
  "overridden_by": null, "override_reason": null, "overridden_at": null, "ts": "…" }

// GET /api/metrics — 7 метрик ТЗ + TTFR раздельно + override + recall (ключи см. api-client.ts)
```

Полный список — 28 путей — в `openapi.json`, либо на живом сервере `/docs`.

---

## 3. Готовые потоки (то, что обычно рисует фронт)

**Чат клиента**
1. `POST /api/messages {client_id, text}` → рисуем `messages[]`.
2. Если `phase == "INTERIM"` — показать «принял, уточняю», затем дописать финальный ответ поллингом
   `GET /api/conversations/{id}/messages?after=<последний id>`.
3. Если `phase == "ACK"` — это Smooth & Hold: показать эмпатичный текст и завести таймер ожидания менеджера.

**Дашборд менеджера**
1. `GET /api/conversations?status=HOLD` — очередь.
2. `GET /api/notifications?unread_only=true` — колокольчик (после показа — `POST /api/notifications/{id}/read`).
3. `POST /api/conversations/{id}/takeover {manager}` → показываем `handoff` (саммари на 30 секунд чтения).
4. `POST /api/conversations/{id}/manager-reply {text}` — ответ клиенту; `POST /api/decisions/{id}/override {reason}` — правка решения ИИ.

**Смена политики (эффектный демо-момент)**
1. `GET /api/policy` → показать текущий `version` и `discounts.max_pct`.
2. `PUT /api/policy {patch: {discounts: {max_pct: 10}}, author: "admin", note: "решение комитета"}`.
3. Отправить тот же запрос клиента в `POST /api/messages` → ответ уже по новой политике, без перезапуска.
4. `GET /api/policy/history` — аудит версий.

---

## 4. Чего в API пока нет (честно, чтобы не было сюрпризов)

| Что | Как жить сейчас | Сколько работы добавить |
|---|---|---|
| WebSocket/SSE | поллинг 1–5 с | 1–2 дня (broadcast из движка) |
| Авторизация клиента и роли менеджеров | закрытый контур + `X-Admin-Token` для админки | 2–3 дня (JWT + роли) |
| Пагинация по курсору | `limit` + фильтр `status`/`channel` | 1 день |
| Версия API `/api/v1` | деплой фронта и бэка вместе | 1 день |
| Идемпотентность `POST /api/messages` | не дублировать отправку на клиенте | 0.5 дня (`Idempotency-Key`) |

---

## 5. Как проверить, что соединение живое (5 минут)

```powershell
# 1. бэкенд
cd ai-native-srm
python scripts\seed_demo.py
python -m uvicorn app.main:app --port 8000

# 2. API отвечает
curl http://localhost:8000/api/health          # {"status":"ok","provider":"mock","policy_version":"1"}

# 3. сайт видит API (вариант A)
copy /E "C:\путь\к\сайту\dist" frontend
# .env: FRONTEND_DIR=./frontend
python -m uvicorn app.main:app --port 8000     # открыть http://localhost:8000/
```

Контрольный список стыковки:

- [ ] `GET /api/health` из браузера/клиента возвращает `ok`;
- [ ] `POST /api/messages` с `client_id=1` отдаёт текст из каталога (значит, данные и LLM-слой живы);
- [ ] спор («Вы обманули с ценой…») даёт `phase: ACK` и диалог в статусе `HOLD`;
- [ ] `GET /api/notifications` показывает уведомление менеджеру;
- [ ] `PUT /api/policy` меняет `max_pct`, и следующий запрос клиента отвечает по новой версии;
- [ ] сайт открыт с того же origin (вариант A), `/api/*` не перехватывается страницей;
- [ ] в `.env` выбран `LLM_PROVIDER=mock` для демо без интернета.

---

## 6. Что делать, если схема сайта не совпала с API

Наиболее частые расхождения и как их закрыть **без переписывания бэкенда**:

1. **Другие имена полей** (`messages` vs `items`, `client_name` vs `clientName`) — адаптер на стороне
   сайта (одна функция `map(x)`) или бэкенд-DTO слой, если полей много.
2. **Сайт ожидает свои эндпоинты** (`/api/chat/send`) — добавить тонкий роутер-обёртку в `app/routers/`,
   который вызывает те же `core`-функции. Это ~20 строк и не трогает логику.
   Живой пример — мост `/api/site/*` для сайта `frnt/` (см. раздел 7).
3. **Сайт хочет «стриминг» ответа** — пока отдавайте `INTERIM`-фазу как «печатает…», а полноценный
   SSE/WS вынести в отдельный шаг.
4. **Другой формат дат/чисел** — форматировать на фронте; API отдаёт ISO и сырые числа, чтобы не терять точность.

---

## 7. Сделано для сайта `frnt/` («ЛОСЬ»): мост `/api/site/*`

Сайт коллег уже подключён к этому бэкенду, и это самый показательный пример «варианта, когда схема
не совпала»: Next-приложение сайта обращается к своим путям (`/api/conversations`, `/api/policy`, …)
в camelCase и со строковыми id (`dlg-0001`, `C-1001`). Переписывать интерфейс не понадобилось —
на стороне бэкенда появилась отдельная поверхность, повторяющая их контракт:

| Слой | Где | Что делает |
|---|---|---|
| Маппинг объектов | `app/adapters/site.py` | клиенты, диалоги, сообщения, решения, саммари, уведомления, инциденты, Policy, trace |
| Маппинг метрик | `app/adapters/site_metrics.py` | их нарезка метрик (share < 100 мс, гистограмма категорий, блокировки V1/V2) на тех же таблицах |
| Ручки | `app/routers/ui_bridge.py` | 20 маршрутов `/api/site/*`: диалоги и лента, takeover/reply/resolve, override решения, Policy + история + симулятор, уведомления, инциденты, метрики, deals, сброс демо, health |
| Тесты | `tests/test_ui_bridge.py` | контракт целиком + шесть сценариев жюри, отправленных «как со страницы чата» |

Бизнес-логики в мосте нет: он вызывает те же `core.ai_engine`, `core.handoff`, `core.policy`,
поэтому поведение сайта и Telegram-бота совпадает по построению, а не по договорённости.

Как это выглядит в их проекте (`frnt/src/server/backend.ts`): каждый `src/app/api/**/route.ts`
сначала проксирует запрос в этот бэкенд (`CRM_BACKEND_URL`) и только при недоступности отвечает
встроенным демо-движком — то есть у сайта остаётся рабочий офлайн-фоллбек.

```powershell
# бэкенд (источник истины)
cd ai-native-srm
python scripts\seed_demo.py
python -m uvicorn app.main:app --port 8030

# сайт — спрашивает бэкенд, при недоступности отвечает своим демо-движком
cd ..\frnt
$env:CRM_BACKEND_URL = "http://127.0.0.1:8030"
npm run dev          # http://localhost:3000 ; GET /api/health покажет "engine":"backend"
```

Что это даёт на защите: одинаковая Policy (тиры 50 000 ₽ / 5 % и 150 000 ₽ / 12 %, бонус
постоянному клиенту 3 %, SLA 1 ч / 4 ч), одни и те же заказы и серийники, одна история версий
политики и один журнал решений — иными словами, «скидка 8 %» в интерфейсе менеджера считается
тем же кодом, что и в пайплайне.

---

## 8. Авторизация сотрудников (роли менеджер/администратор)

Сайт закрыт логином: пароли сотрудников проверяются на бэкенде, интерфейс лишь хранит выданный
токен в httpOnly-cookie и передаёт его в заголовке `X-Auth-Token`.

| Ручка | Кто может | Что делает |
|---|---|---|
| `POST /api/site/auth/login` | все (публичная) | `{login, password}` → `{token, expiresAt, user}`; ошибки: 401 «Неверный логин или пароль», 403 «Учётка отключена» |
| `POST /api/site/auth/logout` | сотрудник | отзывает сессию |
| `GET /api/site/auth/me` | сотрудник | текущий пользователь и его роль |
| `GET/POST /api/site/users` | администратор | список и создание сотрудника (`login`, `password`, `name`, `role`) |
| `PATCH/DELETE /api/site/users/{id}` | администратор | имя, роль, пароль, отключение учётки |

Правила доступа к остальной поверхности: `/api/site/health` — публичный, всё прочее — под сессией,
а `/api/site/policy*` и `/api/site/demo/reset` — только администратор. Роль проверяется на бэкенде
(`core/auth.py`), поэтому обойти запрет через интерфейс нельзя — он лишь прячет разделы.

Что важно знать про сессии:

* пароли хранятся как PBKDF2-HMAC-SHA256 с солью на пользователя; в БД нет ни открытых паролей,
  ни открытых токенов (только SHA-256 токена), TTL сессии — 12 часов;
* смена пароля, смена роли и отключение учётки отзывают все сессии сотрудника;
* нельзя отключить себя и нельзя оставить систему без активного администратора;
* демо-учётки первого запуска — `admin / admin` и `manager / manager`
  (переопределяются `ADMIN_PASSWORD`, `MANAGER_PASSWORD`);
* кнопка «Сбросить демо-данные» очищает диалоги, заявки и политику, но **не** учётные записи,
  иначе администратор вылетал бы на экран входа прямо во время демонстрации.

Проверка за минуту (`ai-native-srm` запущен на :8030):

```bash
TOKEN=$(curl -s -X POST localhost:8030/api/site/auth/login \
  -H 'Content-Type: application/json' -d '{"login":"admin","password":"admin"}' | python -c "import json,sys;print(json.load(sys.stdin)['token'])")
curl -s localhost:8030/api/site/policy -H "X-Auth-Token: $TOKEN" | head -c 200   # 200 — админ видит политику
curl -s -o /dev/null -w '%{http_code}\n' localhost:8030/api/site/conversations    # 401 — без токена закрыто
```

Фронтенд-контур (`frnt/`): сессия в httpOnly-cookie `los_session`, guard в `src/proxy.ts`
(без сессии страницы уводят на `/login?next=…`, `/api/*` отдают 401, `/api/health` и вход — публичны);
серверный прокси добавляет `X-Auth-Token` к запросам в `/api/site/*`. Когда бэкенд выключен, сайт
отвечает демо-движком, но **права остаются прежними**: `src/server/users.ts::mockAdminGuard` закрывает
`/api/policy*` и `/api/demo/reset` тем же правилом «только администратор».

Тесты: `tests/test_auth.py` (13 проверок — роли, сессии, управление учётками, секреты в БД)
и ролевые проверки в `tests/test_ui_bridge.py`.