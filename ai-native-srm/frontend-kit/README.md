# Frontend-kit: всё, что нужно сайту, чтобы подключиться к API

| Файл | Что это |
|---|---|
| `openapi.json` | машинный контракт (28 путей). Годится для генерации типов: `npx openapi-typescript openapi.json -o src/api-types.ts` |
| `api-client.ts` | готовый клиент без зависимостей: чат клиента, дашборд менеджера, политика, метрики, уведомления |

Подробное описание контракта, потоки экранов и чек-лист стыковки — в `../docs/frontend_integration.md`.

## Быстрый старт

```ts
import { CrmApi } from "./api-client";

const api = new CrmApi(import.meta.env.VITE_API_URL ?? "");   // "" — тот же origin

// чат клиента
const answer = await api.sendMessage({ clientId: 1, text: "Сколько стоит МФУ PX-M4200?" });
console.log(answer.messages[0].text);

// дашборд менеджера
const queue = await api.conversations("HOLD");
const notes = await api.notifications(true);
await api.takeover(queue[0].id, "Анна");
await api.managerReply(queue[0].id, "Елена, я взяла ваш вопрос, скидку согласую сегодня до 18:00.", "Анна");
```

Если сайт отдаётся тем же FastAPI (`FRONTEND_DIR`), `baseUrl` можно оставить пустым — запросы пойдут
на тот же origin, CORS не нужен.

## Обновление контракта

```bash
cd ai-native-srm
python -c "import sys; sys.path.insert(0,'.'); from app.main import app; import json; open('frontend-kit/openapi.json','w').write(json.dumps(app.openapi(), ensure_ascii=False, indent=2))"
```
