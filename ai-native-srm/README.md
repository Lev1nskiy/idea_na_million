# AI-native CRM — рабочая папка проекта

Подробный README для разработчика: как запускать, тестировать и расширять код.
Обзор проекта и демо для жюри — в [корневом README](../README.md).

Диалоговый ИИ-ассистент отдела продаж: сам решает типовые обращения (каталог, скидки,
гарантия, доставка), а в спорных ситуациях переходит в **Smooth & Hold** — держит клиента
и передаёт менеджеру готовое саммари за время «30 секунд чтения».

Главный принцип: **LLM формулирует, но не решает**. Категория обращения, маршрут, размер
скидки и сроки считаются кодом из Policy Matrix и tools; модель только переписывает факты
в ответ. Поэтому «ИИ выдумал скидку 99%» здесь невозможно — числа валидируются белым списком.

## Быстрый старт (Windows / PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item .env.example .env          # LLM_PROVIDER=auto: без ключей работает mock, ключи не нужны
python scripts\seed_demo.py          # 6 клиентов, их заказы и Policy Matrix v1

python -m uvicorn app.main:app --port 8000     # API + Swagger на http://localhost:8000/docs
python -m streamlit run ui\Home.py             # демо-стенд на http://localhost:8501
```

Проверить, что всё живо: `curl http://localhost:8000/api/health` → `{"status":"ok"}`.

### Telegram-бот (необязательно)

Единственный канал клиента: меню из восьми разделов — профиль, обращения, чат с ИИ, вызов
менеджера, помощь с базой знаний, каталог, настройки. Клиент не ходит на сайт: там работает
менеджер и видит ту же переписку.

```powershell
python -m channels.mock_telegram --demo      # весь диалог в терминале: без токенов, сети и модели
python -m channels.mock_telegram             # то же, но интерактивно (mock-CRM и mock-ИИ)
# .env: TG_LEAD_BOT_TOKEN=<токен от @BotFather>, TG_MANAGER_CHAT_ID=<id менеджера>
python -m channels.telegram_lead_bot --check # настройки, бот в Telegram, API, провайдер LLM
python -m channels.telegram_lead_bot         # бот: меню, обращения, чат с ИИ, доставка ответов
python -m channels.telegram_lead_bot --drop-webhook      # если запуск падал с TelegramConflictError
python scripts\check_telegram.py                         # почему не приходят данные из Telegram
python scripts\check_lead_pipeline.py --site http://localhost:3000   # приёмка канала бот → CRM → сайт
python scripts\bind_telegram.py --list      # кто из клиентов привязан к Telegram
```

Бот — тонкий адаптер: он только доставляет сообщения в `/api/messages` и показывает ответы
из `/api/outbox`. Вся логика остаётся в `core/`, поэтому бот не «знает» ни про скидки, ни про политику.

### Облачные LLM: GigaChat и YandexGPT (необязательно)

Цепочка провайдеров: **GigaChat → YandexGPT → Local → Mock**. Модель формулирует текст, числа и решения
считает код — поэтому подключение LLM не меняет поведение движка, только качество речи. Без ключей
система полностью работоспособна на mock, а ключи включают облако с первого же запроса:

```powershell
pip install -r requirements-llm.txt      # SDK GigaChat (YandexGPT работает без пакетов)
python scripts\llm_check.py             # проверить ключи, задержку и модели за минуту
```

```dotenv
LLM_PROVIDER=auto                        # или gigachat | yandex | gigachat,yandex — явный порядок
GIGACHAT_CREDENTIALS=<ключ авторизации>  # личный кабинет GigaChat API
YANDEX_API_KEY=<API-ключ>                # сервисный аккаунт с ролью ai.languageModels.user
YANDEX_FOLDER_ID=<каталог>
```

**Бесплатный вариант** — локальная модель с открытыми весами (GigaChat-20B-A3B под MIT,
GigaChat 3 Lightning, YandexGPT-5-Lite-8B): `LOCAL_MODEL=<модель>` и запущенный Ollama, никаких
ключей и лимитов, персональные данные не покидают машину.

Полная инструкция — где брать ключи, как поставить сертификат НУЦ Минцифры, что значит каждая
переменная, команды для локального запуска и что делать при 401/403/429 — в
**[docs/llm_setup.md](docs/llm_setup.md)**.
Состояние цепочки видно в `/api/health` (`llm`, `llm_ready`, `llm_missing`).

### Подключение внешнего фронтенда (сайт на React/Vue/статике)

```powershell
# сайт собран в dist/ — API отдаст его на том же origin (один URL, без CORS)
Copy-Item -Recurse "C:\путь\к\сайту\dist" frontend
# .env: FRONTEND_DIR=./frontend
python -m uvicorn app.main:app --port 8000     # сайт на /, API на /api, Swagger на /docs
```

Контракт и готовый клиент — `docs/frontend_integration.md` и `frontend-kit/`.

### Docker

```powershell
Copy-Item .env.example .env
docker compose up --build                   # API :8000 + UI :8501
docker compose --profile bot up --build     # + Telegram-бот
docker compose run --rm backup              # снимок БД с ротацией 7 копий
```

База лежит в volume `crm-data` (переживает пересборку), справочники монтируются из `./data`,
бэкапы — в `./backups` (см. `deploy/cron.example` для ежедневного cron).

## Демо-сценарии

Пять сценариев защиты, которые стоит показать (и на которые есть автотесты):

1. **FAQ** — цена и наличие по каталогу.
2. **Скидка в рамках vs сверх политики** — максимум предлагается один раз, настаивание → HOLD.
3. **Горячая смена политики** — 15% → 10% применяется к следующему сообщению, без перезапуска.
4. **Гарантия** — серийник найден → замена за 24 ч; не найден → сбор данных, а не отказ.
5. **Prompt Injection** — детерминированный отказ, флаг `suspicious`, инцидент в логе.

Разбор по шагам и то, что видно на экране, — в `docs/pitch.md`.

## Структура

```
core/       логика: perception → understanding → decision → action → guardrails → learning
           (policy, tools, smooth_hold, handoff, templates, guardrails, privacy, rag, llm/*)
app/        FastAPI: /api/messages, conversations, deals, clients, policy, metrics, admin
channels/   адаптеры транспорта: telegram_lead_bot (aiogram 3 — бот с меню),
            bot_flow/bot_ui (диалог и экраны), mock_crm/mock_telegram (офлайн-прогон),
            lead_flow (анкета без aiogram), manager_feed, email_imap (заглушка)
ui/         Streamlit-стенд: чат клиента, дашборд менеджера, политика, метрики безопасности
scripts/    seed_demo, export_state, import_state, db_backup, bind_telegram, chat_cli
data/       каталог, клиенты и заказы, шаблоны КП, база знаний, Устав, policy_seed
docs/       architecture.md, llm_choice.md, pitch.md, api.md, data_transfer.md, lead_bot.md
frontend-kit/ openapi.json + api-client.ts (TS) для подключения внешнего сайта
```

Зависимости идут только «вниз»: `ui → app → core → data`. Ядро ничего не знает про
FastAPI, Streamlit и Telegram — его можно запускать из CLI, тестов и любой другой обвязки.

## Тесты

```powershell
python -m pytest -q                  # 441 тест, сеть не нужна (провайдеры и каналы подменяются)
python -m pytest tests\test_scenarios.py -q -v
```

Покрыто: движок решений и граница C/I, guardrails (числа, инъекции, ПДн, тон), Policy Matrix,
tools (включая кросс-клиентские утечки), метрики, API и админ-перенос данных, 5 демо-сценариев,
экспорт → импорт в чистую БД, Telegram-канал (на фейковом API-клиенте и с aiogram-dispatcher'ом).

Минимальный набор для проверки перед показом:

```powershell
python -m pytest tests\test_scenarios.py tests\test_bot_flow.py tests\test_bot_api.py -q  # сценарии и бот
python -m channels.telegram_lead_bot --check        # бот видит API и провайдера LLM
python -m channels.mock_telegram --demo             # весь диалог бота в терминале
```

## Документация

* `docs/architecture.md` — слои, путь сообщения, **таблица «Статья Устава → механизм в коде»**, деплой.
* `docs/llm_setup.md` — **подключение GigaChat и YandexGPT**: ключи, scope, сертификат, диагностика.
* `docs/llm_choice.md` — сравнение провайдеров, стратегия промптов, план бенчмарка.
* `docs/pitch.md` — проблема, решение, сценарий демо, экономика, честные допущения.
* `docs/api.md` — все эндпоинты и порядок показа демо.
* `docs/data_transfer.md` — перенос состояния между контурами (CLI, admin-API, кнопки на странице «Данные»).
* `docs/lead_bot.md` — Telegram-бот-анкета: заявка → карточка клиента.

## Состояние проекта

| Шаг | Что | Статус |
|---|---|---|
| 1 | фундамент: модели, БД, каталог, CLI | готов |
| 2 | LLM-слой: mock / GigaChat / YandexGPT + fallback (подключение облака — `docs/llm_setup.md`) | готов |
| 3 | guardrails, RAG, метрики | готов |
| 4 | Smooth & Hold, handoff, шаблоны | готов |
| 5 | FastAPI + Streamlit-стенд + перенос данных | готов |
| 6 | Telegram-бот, Docker, демо-сценарии, документация | готов |

## Ограничения (честно)

* UI — демо-стенд, а не production-фронтенд: авторизация только у админ-операций (`X-Admin-Token`).
* Метрики на демо-данных сняты на `mock`-провайдере: это проверка контура, а не замер качества LLM.
* E-mail-канал и векторный RAG не реализованы (первый — заглушка с планом, второй — лексический поиск).
* `.env` и `crm.db` не в репозитории; не запускайте `git clean -x`, иначе потеряете локальные данные.
