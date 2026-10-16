"use client";

// Страница «Данные»: перенос базы между контурами кнопками.
//
// Кнопки ничего не решают сами — они дёргают ручки /api/data/*, а те проксируют запрос
// на бэкенд (app/routers/data_api.py). Правила переноса одни и те же для сайта, admin-API
// и CLI, поэтому дамп с сайта принимается скриптом и наоборот.
//
// Доступ — только администратор: менеджеру показываем объяснение, а не пустой экран.

import { useCallback, useEffect, useRef, useState } from "react";
import { Database, Download, FileJson, HardDriveDownload, ShieldAlert, Upload } from "lucide-react";
import { api, type DataSummary } from "@/lib/api";
import { Btn, Chip, Glass, PageHeader, cx } from "@/components/ui";

interface ImportResult {
  ok: boolean;
  mode?: string;
  dry_run?: boolean;
  created?: number;
  updated?: number;
  skipped?: number;
  warnings?: string[];
  error?: string;
}

const TABLE_LABEL: Record<string, string> = {
  clients: "Клиенты",
  conversations: "Диалоги",
  messages: "Сообщения",
  deals: "Сделки",
  notifications: "Уведомления",
  events: "События",
  users: "Сотрудники",
};

const fmtBytes = (bytes: number) => {
  if (!bytes) return "0 Б";
  const units = ["Б", "КБ", "МБ", "ГБ"];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
};

export default function DataPage() {
  const [allowed, setAllowed] = useState<boolean | null>(null);
  const [summary, setSummary] = useState<DataSummary | null>(null);
  const [masked, setMasked] = useState(true);
  const [mode, setMode] = useState<"merge" | "replace">("merge");
  const [dryRun, setDryRun] = useState(true);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Все запросы идут через api.… (siteFetch): он прикладывает токен предпросмотра, иначе
  // внутри фрейма cookie не отдаётся и страница получала бы 401.
  const loadSummary = useCallback(async () => {
    try {
      setSummary(await api.dataSummary());
    } catch {
      setNotice("Бэкенд не отвечает — состояние БД не прочитано.");
    }
  }, []);

  useEffect(() => {
    let alive = true;
    api.me()
      .then(({ user }) => {
        if (!alive) return;
        const isAdmin = user.role === "admin";
        setAllowed(isAdmin);
        if (isAdmin) void loadSummary();
      })
      .catch(() => { if (alive) setAllowed(false); });
    return () => { alive = false; };
  }, [loadSummary]);

  /** Скачивание: файл получаем запросом, дальше отдаём его браузеру ссылкой с download. */
  const download = async (kind: "export" | "backup") => {
    setBusy(kind);
    setNotice(null);
    try {
      const res = kind === "export" ? await api.dataExport(masked) : await api.dataBackup();
      if (!res.ok) {
        const data = (await res.json().catch(() => ({}))) as { error?: string };
        setNotice(data.error ?? `Не удалось скачать (${res.status}).`);
        return;
      }
      const blob = await res.blob();
      const name = (res.headers.get("content-disposition")?.match(/filename="?([^";]+)/)?.[1])
        ?? (kind === "export" ? "crm-state.json" : "crm-backup.sqlite3");
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = name;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(link.href);
      setNotice(`Файл ${name} (${fmtBytes(blob.size)}) сохранён.`);
    } catch {
      setNotice("Скачивание не удалось: бэкенд не отвечает.");
    } finally {
      setBusy(null);
    }
  };

  const upload = async () => {
    if (!file) {
      setNotice("Сначала выберите JSON-дамп.");
      return;
    }
    setBusy("import");
    setResult(null);
    setNotice(null);
    try {
      const res = await api.dataImport(file, { mode, dryRun });
      const data = (await res.json().catch(() => ({}))) as ImportResult;
      if (!res.ok) {
        setResult({ ok: false, error: data.error ?? `Импорт отклонён (${res.status}).` });
        return;
      }
      setResult(data);
      if (!dryRun) void loadSummary();
    } catch {
      setResult({ ok: false, error: "Импорт не выполнен: бэкенд не отвечает." });
    } finally {
      setBusy(null);
    }
  };

  if (allowed === false) {
    return (
      <div className="mx-auto max-w-3xl pt-10">
        <Glass className="p-6">
          <div className="flex items-center gap-3">
            <ShieldAlert className="text-amber" size={20} />
            <h1 className="font-display text-lg font-semibold">Перенос базы — только администратору</h1>
          </div>
          <p className="mt-3 text-[13px] leading-relaxed text-dim">
            Дамп содержит данные клиентов и переписку. Менеджеру эта страница не нужна: заявки,
            сделки и уведомления он видит в дашборде. Если доступ нужен — попросите администратора
            выдать роль «администратор» в разделе «Сотрудники».
          </p>
        </Glass>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader
        title="Данные"
        sub="База целиком: скачать дампом или снимком SQLite, загрузить дамп из другого контура. Тот же перенос, что и в admin-API и CLI — правила одни."
        right={summary && <Chip tone="mint">Политика v{summary.policyVersion || "—"}</Chip>}
      />

      <Glass className="p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-[13px] font-medium">
            <Database size={16} className="text-acc" /> Состояние базы
          </div>
          <button onClick={() => void loadSummary()} className="text-[12px] text-dim underline hover:text-ink">
            обновить
          </button>
        </div>
        {summary ? (
          <>
            <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
              {Object.entries(summary.tables).map(([table, count]) => (
                <div key={table} className="rounded-xl border border-line/50 px-3 py-2">
                  <div className="text-[11px] uppercase tracking-wide text-dim">{TABLE_LABEL[table] ?? table}</div>
                  <div className="font-display text-lg">{count}</div>
                </div>
              ))}
            </div>
            <p className="mt-3 text-[12px] text-dim">
              Файл: <span className="text-ink/80">{summary.dbFile}</span> · размер {fmtBytes(summary.dbSizeBytes)}
            </p>
          </>
        ) : (
          <p className="mt-3 text-[13px] text-dim">Читаю состояние БД…</p>
        )}
      </Glass>

      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <Glass className="p-5">
          <div className="flex items-center gap-2 text-[13px] font-medium">
            <Download size={16} className="text-acc" /> Экспорт
          </div>
          <p className="mt-2 text-[12px] leading-relaxed text-dim">
            Дамп JSON — переносится между контурами и читается скриптом <code>scripts/import_state.py</code>.
            Снимок SQLite — страховка перед импортом и точная копия базы.
          </p>
          <label className="mt-3 flex items-center gap-2 text-[12px] text-dim">
            <input type="checkbox" checked={masked} onChange={(e) => setMasked(e.target.checked)} />
            Маскировать персональные данные (телефоны, e-mail)
          </label>
          {!masked && (
            <p className="mt-2 rounded-lg border border-amber/40 bg-amber/5 px-3 py-2 text-[11px] text-amber">
              Дамп без маскирования — только для закрытого контура: файл содержит контакты клиентов.
            </p>
          )}
          <div className="mt-4 flex flex-wrap gap-2">
            <Btn onClick={() => void download("export")} disabled={busy !== null}>
              <FileJson size={14} /> {busy === "export" ? "Готовлю…" : "Скачать дамп"}
            </Btn>
            <Btn tone="ghost" onClick={() => void download("backup")} disabled={busy !== null}>
              <HardDriveDownload size={14} /> {busy === "backup" ? "Делаю снимок…" : "Скачать БД"}
            </Btn>
          </div>
        </Glass>

        <Glass className="p-5">
          <div className="flex items-center gap-2 text-[13px] font-medium">
            <Upload size={16} className="text-acc" /> Импорт
          </div>
          <p className="mt-2 text-[12px] leading-relaxed text-dim">
            Загрузка дампа JSON. Сначала прогоните с включённой проверкой: она показывает, что
            изменится, и ничего не записывает. Импорт атомарный — при ошибке база остаётся прежней.
          </p>
          <div className="mt-3 flex flex-col gap-2 text-[12px] text-dim">
            <div className="flex gap-3">
              <label className="flex items-center gap-1.5">
                <input type="radio" checked={mode === "merge"} onChange={() => setMode("merge")} /> дополнить
              </label>
              <label className="flex items-center gap-1.5">
                <input type="radio" checked={mode === "replace"} onChange={() => setMode("replace")} /> заменить
              </label>
            </div>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />
              проверить без записи
            </label>
          </div>
          {mode === "replace" && !dryRun && (
            <p className="mt-2 rounded-lg border border-danger/40 bg-danger/5 px-3 py-2 text-[11px] text-danger">
              Замена стирает текущие записи и подменяет их данными из файла. Сделайте снимок БД заранее.
            </p>
          )}
          <input
            ref={inputRef}
            type="file"
            accept="application/json,.json"
            onChange={(e) => { setFile(e.target.files?.[0] ?? null); setResult(null); }}
            className="mt-3 w-full cursor-pointer rounded-lg border border-line/50 px-3 py-2 text-[12px] text-dim file:mr-3 file:rounded-md file:border-0 file:bg-acc/15 file:px-2 file:py-1 file:text-ink"
          />
          <div className="mt-3 flex flex-wrap gap-2">
            <Btn tone={mode === "replace" && !dryRun ? "danger" : "acc"} onClick={() => void upload()} disabled={busy !== null || !file}>
              <Upload size={14} /> {busy === "import" ? "Загружаю…" : dryRun ? "Проверить дамп" : "Загрузить в базу"}
            </Btn>
            {file && <button onClick={() => { setFile(null); if (inputRef.current) inputRef.current.value = ""; }} className="text-[12px] text-dim underline">сбросить файл</button>}
          </div>
        </Glass>
      </div>

      {notice && (
        <Glass className="mt-4 px-5 py-3 text-[12px] text-dim">
          <span className="text-ink/80">Готово: </span>{notice}
        </Glass>
      )}

      {result && (
        <Glass className={cx("mt-4 p-5", result.ok ? "" : "border-danger/40")}>
          <div className="text-[13px] font-medium">
            {result.ok ? (result.dry_run ? "Проверка пройдена — база не изменена" : "Импорт выполнен") : "Импорт не выполнен"}
          </div>
          {result.error && <p className="mt-2 text-[12px] text-danger">{result.error}</p>}
          {result.ok && (
            <p className="mt-2 text-[12px] text-dim">
              создано: {result.created ?? 0} · обновлено: {result.updated ?? 0} · пропущено: {result.skipped ?? 0}
              {result.mode ? ` · режим: ${result.mode === "replace" ? "замена" : "дополнение"}` : ""}
            </p>
          )}
          {!!result.warnings?.length && (
            <ul className="mt-2 list-disc pl-5 text-[12px] text-amber">
              {result.warnings.map((w) => <li key={w}>{w}</li>)}
            </ul>
          )}
        </Glass>
      )}

      <Glass className="mt-4 p-5">
        <div className="text-[13px] font-medium">Чем это отличается от ручек админа</div>
        <p className="mt-2 text-[12px] leading-relaxed text-dim">
          Ничем по существу: сайт ходит в те же правила переноса (<code>app/transfer.py</code>), просто
          под сессией администратора, а не по токену <code>X-Admin-Token</code>. Скрипты для сервера —
          <code> scripts/export_state.py</code>, <code>scripts/import_state.py</code>, <code>scripts/db_backup.py</code>;
          порядок работы описан в <code>docs/data_transfer.md</code>.
        </p>
      </Glass>
    </div>
  );
}
