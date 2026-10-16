# Архитектура AI-native CRM

Назначение документа: объяснить, **из чего состоит система, почему она так разделена и где
именно в коде живёт каждая статья Устава**. Документ один для команды и для заказчика.

---

## 1. Слои и правило зависимостей

```
app/       FastAPI: HTTP-слой, роутеры, сессии БД              (транспорт + хранение)
channels/  адаптеры каналов: telegram_lead_bot (aiogram 3, бот с меню),
           bot_flow/bot_ui (диалог и экраны без aiogram), mock_crm/mock_telegram (mock-режим)
ui/        Streamlit-стенд демо: клиент, менеджер, политика, метрики
core/      вся бизнес-логика: perception → understanding → decision → action → guardrails
scripts/   seed_demo, export_state, import_state, db_backup, bind_telegram, chat_cli
data/      Устав, каталог, справочник клиентов, шаблоны КП, база знаний, seed политики
```

Главное правило: **зависимости идут только в одну сторону**.

```
ui ──HTTP──> app ──> core ──> data
channels ──HTTP──> app            (channels НЕ импортирует core и не знает про скидки)
```

Практическая проверка правила: `grep -rn "from core" channels/` — пусто. Telegram-бот умеет
только доставлять текст в `POST /api/messages` и забирать готовые ответы из `GET /api/outbox`.
Любую логику можно править в `core/`, не касаясь каналов; любой канал можно добавить, не касаясь
логики. Всё, что относится к решению и тексту ответа, живёт в `core/` и запускается из CLI,
тестов, API или бота одинаково.

---

## 1.1 Схема пяти слоёв и Policy

```
                     ┌──────────── POLICY MATRIX (Ст. 2) ────────────┐
                     │ версии, скидки, SLA, remedy, пороги — читается │
                     │ на каждый запрос, без кэша и перезапуска       │
                     └───────────────────────┬──────────────────────┘
                                             │
  L1 PERCEPTION      core/perception.py      │  нормализация + лексический pre-classifier
                                             │  (спор/инъекция за миллисекунды, без LLM)
  L2 UNDERSTANDING   core/understanding.py   │  LLM → структура: интент, сущности, тон, confidence
  L3 DECISION        core/decision.py        │  категория A–I и маршрут AUTONOMOUS / CLARIFY / HOLD
  L4 ACTION          core/action.py          │  tools → блок FACTS → текст LLM → ответ клиенту
  L5 LEARNING        core/learning.py        │  события, метрики ТЗ, подсказки по политике
                                             │
  ── GUARDRAILS (Ст. 1–4) core/guardrails.py │  числа, тон, инъекции, ПДн — на каждом выходе к клиенту
```

Слои соответствуют ТЗ один в один, поэтому архитектурный слайд совпадает с файлами в репозитории,
а каждый слой тестируется отдельно. Policy и Guardrails — сквозные: первый задаёт правила,
второй проверяет любой текст до отправки.

## 2. Путь одного сообщения

```
клиент (Telegram / UI / curl)
   │  POST /api/messages {client_id, text, channel}
   ▼
core/perception.py      нормализация текста, лексический pre-classifier (быстрый, без LLM):
                        риски, спор, агрессия, инъекция
   ▼
core/understanding.py   LLM → структура: интент, сущности, тональность, confidence
                        (это единственное место, где модель «понимает», и её вывод валидируется)
   ▼
core/decision.py        категория A–H/I, порог уверенности, граница C/I, маршрут:
                        AUTONOMOUS | CLARIFY | HOLD | шаблонный ответ F
   ▼
core/action.py          tools читают каталог/заказы/политику → блок FACTS
                        (цены, наличие, скидка, гарантия, доставка, КП-шаблон)
   ▼
LLM (core/llm)          текст ответа ТОЛЬКО из FACTS; числа не генерируются
   ▼
core/guardrails.py      белый список чисел, тон, запрет обещаний, ПДн → нарушение = шаблон
   ▼
app/models + БД         message, decision, deal, event(метрика), incident, notification, outbox
   ▼
GET /api/outbox         канал забирает ответ и отдаёт клиенту
```

Для категории **I** путь другой — и это принципиально: `smooth_hold.ack()` отдаёт
детерминированный ACK **до** любого обращения к модели (Ст. 9, TTFR < 100 мс), а дальше
клиента удерживает `hold_loop`, пока менеджер не сделает takeover.

---

## 3. Ключевые решения и компромиссы

| Решение | Почему так | Компромисс |
|---|---|---|
| Детерминированный шаблон первым ответом в I/F | Устав требует < 100 мс и запрещает LLM в спорных формулировках; LLM добавлял бы 1–3 с и риск выдумать срок | тексты шаблонов жёстче «живого» языка (сглажено эмпатичными формулировками в `templates.py`) |
| Числа только из tools/Policy + белый список | невозможно выдумать цену или скидку; политика обновляется без релиза кода | любое новое число в тексте требует явного источника-факта |
| Категории A–H/I решает код, а не LLM | повторяемость, тестируемость, аудит: каждое решение — строка в `decisions` с причиной | классификация чуть менее «гибкая», чем чистый LLM-роутер |
| Каналы — тонкие HTTP-клиенты | вся логика в одном месте, бот не может обойти guardrails | канал не может сделать «свою» локальную оптимизацию |
| Один образ, три сервиса (compose) | одинаковые зависимости, различается только команда запуска | UI и бот тянут зависимости друг друга (не критично на этом объёме) |
| SQLite по умолчанию | нулевая настройка демо, переносимость одним файлом | для прода — Postgres (меняется только `DB_URL`) |

---

## 4. Структура кода

| Файл | Ответственность |
|---|---|
| `core/perception.py` | нормализация, pre-classifier (лексика из `data/lexicon.json`, тест-набор в `preclassifier_testset.json`) |
| `core/understanding.py`, `core/prompts/understanding.py` | извлечение структуры запроса моделью |
| `core/decision.py` | приоритет категорий, порог уверенности, граница C/I, выбор маршрута |
| `core/action.py` | сборка FACTS, оркестрация ответа A–H |
| `core/tools.py` | каталог, скидка по политике, гарантия по серии/заказу, доставка, КП — всё по `client_id` |
| `core/policy.py` | версионируемая Policy Matrix: скидки, SLA, remedy, пороги (Ст. 2) |
| `core/guardrails.py` | числа, тон, инъекции, ПДн, запрет обещаний |
| `core/smooth_hold.py` | ACK, Smooth & Hold, `hold_loop`, напоминания менеджеру и пинги клиенту |
| `core/handoff.py` | саммари для менеджера ≤ 30 секунд чтения (Ст. 8) |
| `core/templates.py` | все детерминированные тексты (без LLM) |
| `core/learning.py` | события, метрики ТЗ, подсказки по политике |
| `core/privacy.py` | маскирование ПДн в логах/событиях/инцидентах (Ст. 4) |
| `core/rag.py` | поиск по базе знаний (`data/knowledge_base.md`) |
| `core/portability.py` | экспорт/импорт состояния, бэкап SQLite |
| `core/llm/*` | провайдеры: mock (тесты/демо), Gigachat, YandexGPT, общий базовый класс |
| `app/routers/*` | 8 роутеров: messages, conversations, clients, deals, policy, notifications, metrics, admin |
| `channels/telegram_lead_bot.py` | транспорт бота: меню-виджет, inline-навигация, доставка ответов, уведомления менеджеру, `--check` |
| `channels/bot_flow.py` + `bot_ui.py` | диалог бота и экраны: состояния, кнопки, тексты — без aiogram и сети |
| `channels/mock_crm.py` + `mock_telegram.py` | mock-CRM и офлайн-прогон диалога: демонстрация без токенов, сети и модели |
| `app/routers/bot_api.py` | фасад `/api/bot/*`: профиль, обращения, переписка, вызов менеджера |

---

## 5. Статья Устава → механизм в коде

Таблица соответствия: слева требование Устава, справа — где оно реализовано и чем проверено.
Это ответ на вопрос «а вы точно выполнили ТЗ?» — без пересказа Устава словами.

| № | Статья Устава | Механизм в коде | Тесты |
|---|---|---|---|
| 1 | Иерархия истин: системные инструкции > Policy > клиент | `core/prompts/system.py` (приоритет в промпте), `guardrails.validate_output` отклоняет текст, противоречащий фактам | `test_guardrails.py`, `test_output_guardrails.py` |
| 2 | Скидки не захардкожены, берутся из Policy | `core/policy.py` + `core/tools.py:calc_discount`, tiers и лимиты только из `policy_versions` | `test_policy.py`, `test_scenario_2*` |
| 2 | Смена политики применяется немедленно, без перезагрузки | `PUT /api/policy` → новая версия, читается на каждый запрос; решения ссылаются на `policy_version` | `test_scenario_3_policy_change_applies_without_restart` |
| 2 | Числа не генерирует LLM; выход проверяется белым списком | `guardrails.extract_numbers/numbers_in` + `validate_output`, факты из `tools` | `test_output_guardrails.py`, `test_scenario_*` |
| 3 | Инъекция → детерминированный шаблон, без LLM | `decision.py` (ветка F), `templates.f_script` | `test_injection_template_incident_suspicious_without_llm` |
| 3 | Флаг `suspicious` | `conversations.suspicious`, пишется в `decision` | `test_injection_*` |
| 3 | Инцидент в логе безопасности | `security_incidents` (+`raw_text_masked`), `GET /api/security/incidents` | `test_metrics.py`, `test_privacy.py` |
| 3 | Легитимная часть обрабатывается, диалог не прекращается | `guardrails._strip_injection`, `perception` | `test_injection_then_legit_question_*`, `test_dialog_is_not_blocked_after_injection` |
| 4 | Данные другого клиента недоступны | каждый tool принимает `client_id`; `tools.foreign_client_markers` — защита от подстановки чужих имён/товаров | `test_tools.py`, `test_privacy.py` |
| 4 | ПДн не в открытых логах | `core/privacy.py`: `mask_pii`, `mask_payload`, фильтр логов, `text_hash` вместо текста | `test_privacy.py` |
| 5 | Автономия A–H без разрешения менеджера | `decision.py` → `AUTONOMOUS`, `action.py` исполняет действие (карточка, КП, замена) | `test_decision.py`, `test_scenario_1/2/4` |
| 5 | Гарантия: заказ не найден → собрать данные, а не отказать | `tools.check_warranty` → `warranty_data_collection`, `templates.ask_order_details` | `test_scenario_4_warranty_found_and_not_found` |
| 5 | Задержка поставки: компенсация из Policy | `tools.delivery_status` + `policy.delivery_compensation` | `test_tools.py` |
| 5 | Каждое автономное решение в логе и доступно для override | таблица `decisions` + `POST /api/decisions/{id}/override`, `override_rate` в метриках | `test_metrics.py`, `test_api.py` |
| 6 | Спор → Smooth & Hold | `core/smooth_hold.py`: `ack`, `smooth_reply`, статус `HOLD` | `test_dispute_instant_ack_hold_notification_and_handoff` |
| 6 | Граница C/I: одно предложение максимума, настаивание → I | `policy.thresholds.discount_push_limit`, ветка в `decision.py` | `test_scenario_2b_discount_limit_escalates_to_manager`, `test_decision.py` |
| 6 | Запрещено спорить/признавать вину/врать про сроки | `guardrails.validate_output` + белый список чисел в тексте HOLD | `test_smooth_reply_blocks_guilt_commitments_and_invented_timing` |
| 6 | Клиент не остаётся без ответа; уведомления дублируются | `hold_loop` + `notifications`, дублирование до takeover | `test_reminders_duplicate_until_takeover_without_new_rows`, `test_hold_loop_runs_and_stops` |
| 7 | Тон: вежливо, лаконично, эмпатично в HOLD | `templates.py` (детерминированные тексты) + проверки тона в `guardrails` | `test_templates.py`, `test_scenarios.py` |
| 8 | Handoff: саммари ≤ 30 с чтения, без «сырого» диалога | `core/handoff.py:build_summary/_fit_budget`, `summary_word_count` | `test_handoff_summary_fits_30_seconds_of_reading` |
| 8 | SLA озвучивается честно, из Policy | `policy.get_manager_sla`, `templates.sla_phrase` (день/ночь) | `test_ack_follows_policy_sla_day_and_night_and_passes_v6` |
| 9 | 24/7, off-hours coverage | SLA и `work_hours` из политики; `hold_loop` работает всегда; метрика `off_hours_coverage` | `test_policy.py`, `test_metrics.py` |
| 9 | TTFR < 100 мс для I/F; таймер 3.5 с для остальных | мгновенный ACK без LLM; `asyncio.wait_for` + `templates.interim_ack`; метрики `ttfr.*` | `test_dispute_instant_ack_hold_notification_and_handoff`, `test_metrics.py` |

---

## 6. Деплой (Шаг 6)

```
docker compose up --build                 # api :8000 + ui :8501
docker compose --profile bot up --build   # + telegram-бот
docker compose run --rm backup            # снимок crm.db с ротацией 7
```

* Один образ (`Dockerfile`) на три сервиса: различается только команда запуска.
* БД — в volume `crm-data` (`/data/crm.db`), переживает пересборку; справочники монтируются
  из `./data` (правка каталога и Устава без пересборки).
* Бэкап: `scripts/db_backup.py --rotate 7` (консистентный снимок живой SQLite через
  `sqlite3.Connection.backup()`), расписание — `deploy/cron.example`.
* Секреты — только через `.env` (шаблон `.env.example`), в образ не попадают (`.dockerignore`).
* Healthcheck API `/api/health`; ui и bot стартуют после `service_healthy`.
* Монтирование фронтенда (если он есть): `./frontend` → `/app/frontend`, переменная `FRONTEND_DIR`.
  Для этого в `app/frontend.py` есть `mount_spa()`: SPA отдаётся с того же origin, что и API;
  catch-all маршрут регистрируется ПОСЛЕДНИМ (иначе он перехватывает `/api/*` — проверено тестом).

Проверка без Docker (в песочнице разработки Docker недоступен):

```bash
python -c "import yaml,pathlib; yaml.safe_load(pathlib.Path('docker-compose.yml').read_text())"
docker compose config --quiet     # на машине пользователя
```

---

## 7. Что дальше по архитектуре

1. **Postgres** вместо SQLite (одна переменная `DB_URL`) + миграции Alembic — сейчас схема
   создаётся `init_db()`, для прода нужны версионируемые миграции.
2. **Очередь** (Redis/RQ) для `hold_loop` и outbox: сейчас фоновые циклы живут в процессе API,
   при нескольких репликах потребуется распределённая блокировка.
3. **RAG на эмбеддингах** (pgvector) вместо лексического поиска: `core/rag.py` изолирует
   интерфейс, замена не затронет остальное.
4. **E-mail/WhatsApp каналы** — по образцу Telegram-бота (транспорт + `bot_flow` + фасад `/api/bot/*`), без изменений в `core/`.
5. **Наблюдаемость**: событие `provider=<name>` в `events` (сейчас фиксируется latency, но не
   движок), экспорт метрик в Prometheus.

## 8. Ограничения текущей версии

* Аутентификация: только `X-Admin-Token` для админ-операций; UI — демо-стенд без ролей.
* Telegram-бот работает через long polling; для продакшена предпочтителен webhook.
* UI — Streamlit (быстрый демо-стенд), не production-фронтенд.

---

## 9. Метрики: как считаются

Все метрики собираются из БД (`decisions`, `conversations`, `events`, `security_incidents`,
`messages`), а не из отдельного аналитического хранилища: одна и та же выборка даёт одинаковые
цифры в UI, API и тестах. Метрики считает `core/learning.compute_metrics()`.

| Метрика | Формула / источник | Оговорка |
|---|---|---|
| `ttfr` | по группам (`IF_pre_classifier`, `IF_llm`, `hold_followups`, `other`): count, p50, p95, доля ответов быстрее порога | порог 100 мс применяется к I/F, 5 000 мс — к остальным (`SLOW_REPLY_MS`) |
| `auto_decision_rate_pct` | (диалоги без категории I) / (все диалоги) × 100. Категория диалога — **наиболее приоритетная** из встреченных в нём | формула заказчика; override в неё не входит — для этого отдельная метрика |
| `client_retention_in_disputes` | доли споров в статусах HOLD / HANDOFF|RESOLVED / LOST | LOST ставит канал или менеджер: в MVP это прокси, а не детект ухода клиента |
| `price_hallucination` | `intercepted` (блокировки валидатором чисел) и `delivered_hallucinations` — события `validator_bypass` | цель 0: пока счётчик bypass равен нулю, «доставленных» галлюцинаций нет |
| `injection_resistance` | доля диалогов с F, в которых не было «пробития» (нет чисел вне белого списка после инъекции) | инциденты считаются отдельно |
| `off_hours_coverage` | доля сообщений вне рабочих часов (`manager_sla.work_hours`), получивших ответ | рабочие часы — из политики, а не из кода |
| `manager_response_time` | время от уведомления до takeover: медиана/максимум, сколько ждут прямо сейчас | SLA тоже из политики |
| `override_rate` | доля решений, переопределённых менеджером, всего и по категориям | источник — `decisions.override*` + лог правок |
| `pre_classifier_recall` | recall и false positive rate на `data/preclassifier_testset.json` | честное противопоставление: `<100 мс` считается по пойманным pre-classifier |
| `policy_suggestions` | эвристики по метрикам (высокий override в категории, много low-confidence) → предложения правок политики | MVP «обучения» — без реального обучения модели |

Смоук-проверка метрик на демо-стенде: `GET /api/metrics` и страница `4_Security_Metrics` показывают
одни и те же числа, потому что читают один эндпоинт.

## 10. Как показать систему жюри

| Способ | Команда | Когда уместно |
|---|---|---|
| Локально (Windows) | `python -m uvicorn app.main:app --port 8000` + `streamlit run ui/Home.py` | основной вариант на защите, офлайн |
| Docker | `docker compose up --build` | если нужен «одной командой» и чистый контур |
| Публичный HTTPS для жюри | VM + `docker compose up -d` за обратным прокси (Caddy/nginx) или туннель (ngrok/cloudflared) | когда жюри хочет потыкать сами; UI обращается к API по внутренней сети, наружу нужен только :8501 |
| Офлайн-страховка | `LLM_PROVIDER=mock` в `.env` | провайдер недоступен на защите — демо всё равно едет |
