"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlarmClockCheck, Bell, BellRing, CheckCheck, CircleCheckBig, Clock3, Handshake,
  ShieldAlert, Swords, UserRoundCheck, Volume2,
} from "lucide-react";
import { api, type ConvDetail, type ConvListItem, type NotificationDto } from "@/lib/api";
import { useNow, usePolling } from "@/lib/hooks";
import { relTime } from "@/lib/format";
import { Btn, CategoryChip, Chip, Glass, PageHeader, StatusChip, cx, inputCls } from "@/components/ui";
import { Bubble } from "@/components/Bubble";
import type { ConvStatus } from "@/lib/types";

const COLS: { status: ConvStatus; title: string; hint: string }[] = [
  { status: "ACTIVE", title: "Автономно", hint: "ИИ работает сам" },
  { status: "HOLD", title: "HOLD", hint: "ждут подключения менеджера" },
  { status: "HANDOFF", title: "Менеджер", hint: "человек в диалоге" },
];

const NTYPE = {
  DISPUTE: { icon: Swords, tone: "text-danger", label: "Спор" },
  SECURITY: { icon: ShieldAlert, tone: "text-amber", label: "Инъекция" },
  OVERRIDE_DUE: { icon: BellRing, tone: "text-acc2", label: "Нужно решение" },
  REMINDER: { icon: Bell, tone: "text-dim", label: "Напоминание" },
} as const;

export default function DashboardPage() {
  const [list, setList] = useState<ConvListItem[]>([]);
  const [notif, setNotif] = useState<NotificationDto[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ConvDetail | null>(null);
  const [reply, setReply] = useState("");
  const [busy, setBusy] = useState(false);
  const now = useNow(1000);
  const scrollRef = useRef<HTMLDivElement>(null);
  const prevCount = useRef(0);

  const refresh = async () => {
    try {
      const [c, n] = await Promise.all([api.conversations(), api.notifications()]);
      setList(c.conversations);
      setNotif(n.notifications);
      if (n.unread > prevCount.current && typeof window !== "undefined") {
        // визуальный индикатор новых DISPUTE-уведомлений (без звука — автоплей запрещён браузерами)
      }
      prevCount.current = n.unread;
    } catch { /* офлайн */ }
  };
  usePolling(refresh, 3000);

  usePolling(async () => {
    if (selectedId) setDetail(await api.conversation(selectedId));
  }, 2200, [selectedId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [detail?.messages.length]);

  const byStatus = useMemo(() => {
    const m = new Map<ConvStatus, ConvListItem[]>();
    for (const c of COLS) m.set(c.status, []);
    for (const conv of list) {
      if (conv.status === "RESOLVED") continue;
      m.get(conv.status)?.push(conv);
    }
    return m;
  }, [list]);

  const counts = {
    ACTIVE: byStatus.get("ACTIVE")?.length ?? 0,
    HOLD: byStatus.get("HOLD")?.length ?? 0,
    HANDOFF: byStatus.get("HANDOFF")?.length ?? 0,
  };
  const unreadDisputes = notif.filter((n) => n.active && !n.read && n.type === "DISPUTE").length;

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
      await refresh();
      if (selectedId) setDetail(await api.conversation(selectedId));
    } finally { setBusy(false); }
  };

  return (
    <div>
      <PageHeader
        title="Дашборд менеджера"
        sub="Споры, инъекции и переопределения попадают сюда мгновенно. Уведомления дублируются каждые N минут, пока вы не возьмёте диалог (Ст. 9)."
        right={
          <>
            <Chip tone="mint">автономно: {counts.ACTIVE}</Chip>
            <Chip tone="danger" className={counts.HOLD ? "hold-pulse" : ""}>
              <Volume2 size={11} /> HOLD: {counts.HOLD}
              {unreadDisputes > 0 && <span className="mono font-bold">· {unreadDisputes} новых</span>}
            </Chip>
            <Chip tone="acc">менеджер: {counts.HANDOFF}</Chip>
          </>
        }
      />

      <div className="grid gap-4 xl:grid-cols-[300px_330px_minmax(0,1fr)]">
        {/* уведомления */}
        <Glass className="max-h-[calc(100vh-170px)] overflow-hidden">
          <div className="flex items-center justify-between border-b border-edge px-4 py-3">
            <span className="text-[10px] uppercase tracking-[0.18em] text-dim">Уведомления</span>
            <span className="mono text-[10px] text-dim">{notif.filter((n) => n.active).length} активных</span>
          </div>
          <div className="max-h-[calc(100vh-226px)] space-y-2 overflow-y-auto p-2.5">
            {notif.length === 0 && <p className="p-3 text-[11.5px] text-dim">Пока пусто. Спор или инъекция из чата появятся здесь мгновенно.</p>}
            {notif.map((n) => {
              const meta = NTYPE[n.type];
              const Icon = meta.icon;
              return (
                <button
                  key={n.id}
                  onClick={() => setSelectedId(n.convId)}
                  className={cx(
                    "w-full rounded-xl border p-3 text-left transition",
                    n.active ? "border-danger/30 bg-danger/[0.06] hover:border-danger/60" : "border-edge bg-white/[0.02] opacity-55 hover:opacity-90",
                    selectedId === n.convId && "ring-1 ring-acc/50",
                  )}
                >
                  <div className="flex items-center gap-2">
                    <Icon size={13} className={meta.tone} />
                    <span className="text-[10px] font-semibold uppercase tracking-wider text-dim">{meta.label}</span>
                    <span className="mono ml-auto flex items-center gap-1 text-[9.5px] text-dim">
                      <Clock3 size={10} /> ждёт {n.waitingMinutes} мин
                    </span>
                  </div>
                  <p className="mt-1.5 text-[11.5px] leading-snug text-txt/90">{n.text}</p>
                  <div className="mt-2 flex items-center gap-1.5">
                    <div className="flex gap-0.5" title={`уровень эскалации ${n.level}`}>
                      {Array.from({ length: Math.min(n.level, 6) }).map((_, i) => (
                        <span key={i} className={cx("h-1 w-3 rounded-full", i === 0 ? "bg-dim/60" : i < 3 ? "bg-amber" : "bg-danger")} />
                      ))}
                    </div>
                    {n.telegram && <Chip tone="sky" className="px-1.5 py-0 text-[9px]">+ telegram</Chip>}
                    {n.slaBreached && <Chip tone="danger" className="px-1.5 py-0 text-[9px]">SLA просрочен</Chip>}
                    {!n.read && n.active && (
                      <span
                        role="button" tabIndex={0}
                        onClick={(e) => { e.stopPropagation(); void act(() => api.readNotification(n.id)); }}
                        className="ml-auto inline-flex cursor-pointer items-center gap-1 text-[9.5px] text-dim hover:text-txt"
                      >
                        <CheckCheck size={11} /> прочитано
                      </span>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        </Glass>

        {/* колонки диалогов */}
        <div className="space-y-3 overflow-y-auto xl:max-h-[calc(100vh-170px)]">
          {COLS.map((col) => {
            const items = byStatus.get(col.status) ?? [];
            return (
              <Glass key={col.status} className={cx("overflow-hidden", col.status === "HOLD" && items.length > 0 && "edge-glow")}>
                <div className={cx("flex items-center justify-between border-b border-edge px-4 py-2.5", col.status === "HOLD" && items.length > 0 && "bg-danger/[0.07]")}>
                  <div className="flex items-center gap-2">
                    <span className={cx("text-[11px] font-semibold uppercase tracking-wider", col.status === "HOLD" ? "text-danger" : col.status === "ACTIVE" ? "text-mint" : "text-acc")}>
                      {col.title}
                    </span>
                    <span className="text-[10px] text-dim">{col.hint}</span>
                  </div>
                  <span className="mono text-[11px] text-dim">{items.length}</span>
                </div>
                <div className="space-y-1.5 p-2">
                  {items.length === 0 && <p className="px-2 py-2.5 text-[10.5px] text-dim/70">пусто</p>}
                  {items.map((c) => (
                    <button
                      key={c.id}
                      onClick={() => setSelectedId(c.id)}
                      className={cx(
                        "w-full rounded-xl border px-3 py-2.5 text-left transition",
                        selectedId === c.id ? "border-acc/60 bg-acc/10" : "border-transparent hover:bg-white/[0.04]",
                        c.status === "HOLD" && "hold-pulse",
                      )}
                    >
                      <div className="flex items-center gap-2">
                        <span className="truncate text-[12.5px] font-medium">{c.client?.name ?? c.clientId}</span>
                        {c.suspicious && <ShieldAlert size={12} className="shrink-0 text-amber" />}
                      </div>
                      <p className="mt-1 line-clamp-2 text-[10.5px] leading-snug text-dim">{c.lastMessage}</p>
                      <div className="mt-1.5 flex items-center gap-1.5">
                        <CategoryChip category={c.category} withLabel={false} />
                        {c.status === "HOLD" && <Chip tone="danger" className="px-1.5 py-0 text-[9px]">ждёт {c.waitingMinutes} мин</Chip>}
                        <span className="mono ml-auto text-[9.5px] text-dim/70">{relTime(c.lastAt, now)}</span>
                      </div>
                    </button>
                  ))}
                </div>
              </Glass>
            );
          })}
        </div>

        {/* детали диалога */}
        <Glass className="flex max-h-[calc(100vh-170px)] min-h-[480px] flex-col overflow-hidden">
          {!detail ? (
            <div className="grid flex-1 place-items-center p-8 text-center">
              <div>
                <UserRoundCheck size={26} className="mx-auto text-acc/50" />
                <p className="mt-3 max-w-xs text-[12.5px] text-dim">
                  Выберите диалог. Для HOLD-карточек сверху откроется 30-секундное саммари ИИ: суть, требования и рекомендуемый диапазон решения (Ст. 8).
                </p>
              </div>
            </div>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2.5 border-b border-edge px-4 py-3">
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13.5px] font-medium">{detail.client.name}</div>
                  <div className="text-[10.5px] text-dim">{detail.client.contact} · {detail.client.segment} · {detail.client.note}</div>
                </div>
                <CategoryChip category={detail.conversation.category} />
                <StatusChip status={detail.conversation.status} />
                {detail.conversation.status === "HOLD" && (
                  <Btn tone="mint" disabled={busy} onClick={() => void act(() => api.takeover(detail.conversation.id))}>
                    <Handshake size={13} /> Takeover
                  </Btn>
                )}
                {detail.conversation.status !== "RESOLVED" && (
                  <Btn tone="ghost" disabled={busy} onClick={() => void act(() => api.resolve(detail.conversation.id))}>
                    <CircleCheckBig size={13} /> Закрыть
                  </Btn>
                )}
              </div>

              <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4" ref={scrollRef}>
                {detail.handoff && detail.handoff.status !== "resolved" && (
                  <div className="edge-glow glass bg-acc/[0.05] p-4">
                    <div className="flex items-center gap-2 text-[10px] uppercase tracking-[0.18em] text-acc2">
                      <AlarmClockCheck size={12} /> Саммари ИИ · читается за 30 с
                      <Chip tone={detail.handoff.summary.urgency === "high" ? "danger" : detail.handoff.summary.urgency === "medium" ? "amber" : "dim"} className="ml-auto">
                        срочность: {detail.handoff.summary.urgency}
                      </Chip>
                    </div>
                    <p className="mt-2.5 text-[12.5px] font-medium leading-snug">{detail.handoff.summary.essence}</p>
                    <div className="mt-2.5 grid gap-3 md:grid-cols-2">
                      <div>
                        <div className="text-[9.5px] uppercase tracking-wider text-dim">Требования клиента</div>
                        <ul className="mt-1 space-y-0.5">
                          {detail.handoff.summary.demands.map((d) => <li key={d} className="text-[11.5px] text-txt/85">· {d}</li>)}
                        </ul>
                      </div>
                      <div>
                        <div className="text-[9.5px] uppercase tracking-wider text-dim">История</div>
                        <ul className="mt-1 space-y-0.5">
                          {detail.handoff.summary.historyDigest.length
                            ? detail.handoff.summary.historyDigest.map((h) => <li key={h} className="mono text-[10.5px] text-dim">{h}</li>)
                            : <li className="text-[10.5px] text-dim">история пуста</li>}
                        </ul>
                      </div>
                    </div>
                    <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                      {detail.handoff.summary.remedyOptions.map((r) => <Chip key={r} tone="sky">{r}</Chip>)}
                      <Chip tone="mint" className="mono">рекомендация: {detail.handoff.summary.recommendedRange}</Chip>
                    </div>
                  </div>
                )}

                {detail.messages.map((m) => <Bubble key={m.id} m={m} compact />)}

                {detail.decisions.length > 0 && (
                  <div className="rounded-xl border border-edge bg-white/[0.02] p-3">
                    <div className="mb-2 text-[9.5px] uppercase tracking-[0.18em] text-dim">Решения ИИ · переопределение (Ст. 5)</div>
                    <div className="space-y-2">
                      {detail.decisions.slice(-3).map((d) => (
                        <DecisionRow key={d.id} d={d} onOverride={(reason) => void act(() => api.overrideDecision(d.id, reason))} busy={busy} />
                      ))}
                    </div>
                  </div>
                )}
              </div>

              {detail.conversation.status !== "RESOLVED" && (
                <form
                  className="flex items-end gap-2 border-t border-edge p-3"
                  onSubmit={(e) => { e.preventDefault(); if (reply.trim()) void act(() => api.managerReply(detail.conversation.id, reply).then(() => setReply(""))); }}
                >
                  <textarea
                    value={reply}
                    onChange={(e) => setReply(e.target.value)}
                    rows={2}
                    placeholder="Ответ менеджера — клиент получит его в свой чат (hold-loop остановится автоматически)…"
                    className={inputCls}
                  />
                  <Btn disabled={busy || !reply.trim()} className="shrink-0">Отправить</Btn>
                </form>
              )}
            </>
          )}
        </Glass>
      </div>
    </div>
  );
}

function DecisionRow({ d, onOverride, busy }: { d: ConvDetail["decisions"][number]; onOverride: (reason: string) => void; busy: boolean }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  return (
    <div className="rounded-lg border border-edge px-2.5 py-2">
      <div className="flex flex-wrap items-center gap-2 text-[10.5px]">
        <CategoryChip category={d.category} withLabel={false} />
        <span className="mono text-dim">conf {d.confidence.toFixed(2)}</span>
        <span className="mono text-dim">Policy v{d.policyVersion}</span>
        <span className="mono text-[9.5px] text-dim/60">{d.id}</span>
        {d.overriddenBy ? (
          <Chip tone="amber" className="ml-auto px-1.5 py-0 text-[9px]">
            переопределено · {d.overrideReason}
          </Chip>
        ) : (
          <button onClick={() => setOpen(!open)} className="ml-auto text-[9.5px] text-acc2 underline-offset-2 hover:underline">
            override
          </button>
        )}
      </div>
      {open && !d.overriddenBy && (
        <div className="mt-2 flex gap-1.5">
          <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="причина переопределения…" className={cx(inputCls, "py-1.5 text-[11.5px]")} />
          <Btn tone="amber" disabled={busy || !reason.trim()} onClick={() => onOverride(reason)} className="shrink-0 px-2.5 py-1.5 text-[11px]">
            Применить
          </Btn>
        </div>
      )}
    </div>
  );
}
