"use client";

// Предупреждение «сайт работает на демо-данных».
//
// Зачем: сайт умеет работать автономно — если CRM_BACKEND_URL не задан или бэкенд не отвечает,
// он молча отдаёт свои mock-данные. Из-за этой тишины легко решить, что «заявка из бота
// не дошла»: бот пишет в базу бэкенда, а сайт показывает совсем другой демо-стор.
// Поэтому о расхождении говорим прямо в интерфейсе, а не только в /api/health.
//
// Проверка идёт ТОЛЬКО во время работы браузера. Считать `CRM_BACKEND_URL` на этапе сборки
// нельзя: страницы пререндерятся `next build`, и тогда флаг «бэкенда нет» запекается в статичный
// HTML — предупреждение осталось бы висеть даже при живом бэкенде.

import { useEffect, useState } from "react";
import { PlugZap } from "lucide-react";

/** Решение по состоянию движка: нужно ли предупреждение и какое именно. Вынесено для проверки тестом. */
export function backendWarning(engine: string | null | undefined): { offline: boolean } | null {
  if (!engine || engine === "backend") return null;      // пусто/«backend» — сайт на CRM
  return { offline: engine === "mock-fallback" };        // mock-фоллбек = бэкенд был, но упал
}

export function BackendNotice() {
  const [engine, setEngine] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const check = () =>
      fetch("/api/health", { cache: "no-store" })
        .then((r) => r.json())
        .then((data) => { if (alive) setEngine(String(data.engine ?? "")); })
        // сеть отвалилась — считаем, что бэкенда нет: показать лишнее безопаснее, чем промолчать
        .catch(() => { if (alive) setEngine("mock-fallback"); });
    void check();
    const timer = setInterval(check, 30_000); // бэкенд могли поднять или погасить по ходу работы
    return () => { alive = false; clearInterval(timer); };
  }, []);

  const warning = backendWarning(engine);
  if (!warning) return null;

  return (
    <div className="mb-4 flex items-start gap-3 rounded-xl border border-amber/40 bg-amber/[0.07] px-4 py-3">
      <PlugZap size={16} className="mt-0.5 shrink-0 text-amber" />
      <div className="text-[12px] leading-relaxed text-txt/90">
        <span className="font-medium">Сайт работает на демо-данных</span>
        <span className="text-dim">
          {warning.offline
            ? " — CRM-бэкенд не отвечает, показываю встроенный набор. "
            : " — CRM-бэкенд не подключён. "}
          Данные сайта сейчас не связаны с базой, поэтому заявки из Telegram-бота и карточки клиентов
          здесь не появятся.
        </span>
        <div className="mt-1 text-[11.5px] text-dim">
          Лечится адресом бэкенда: запустите сайт с <code className="mono text-acc2">CRM_BACKEND_URL</code>,
          например <code className="mono">CRM_BACKEND_URL=http://127.0.0.1:8000 npm start</code>.
          Проверка: <code className="mono">/api/health</code> → поле <code className="mono">engine</code> должно быть{" "}
          <code className="mono text-mint">backend</code>.
        </div>
      </div>
    </div>
  );
}
