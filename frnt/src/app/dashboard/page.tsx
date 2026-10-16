"use client";

// Рабочее место менеджера: лента уведомлений + чат менеджера.
//
// Клиент на сайте не пишет: его канал — Telegram (клиентский бот) или другой внешний канал.
// Сюда, в дашборд, стекаются все диалоги ИИ с клиентами. Менеджер видит переписку в реальном
// времени, может ответить (ответ автоматически переводит диалог на него) и переопределить
// решение ИИ. Лента уведомлений узкая и компактная — она подсказывает, куда смотреть,
// а основное место занимает сам чат.

import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlarmClockCheck, Bell, BellRing, CheckCheck, ChevronDown, CircleCheckBig, Clock3, Handshake,
  Inbox, ShieldAlert, Swords, UserRoundCheck, UserRoundPlus, Volume2,
} from "lucide-react";
import { api, type ConvDetail, type ConvListItem, type NotificationDto } from "@/lib/api";
import { useNow, usePolling } from "@/lib/hooks";
import { relTime } from "@/lib/format";
import { Btn, CategoryChip, Chip, Glass, PageHeader, StatusChip, cx, inputCls } from "@/components/ui";
import { Bubble } from "@/components/Bubble";
import type { ConvStatus } from "@/lib/types";

/** Статус диалога: подпись и цвет — единые для списка, шапки чата и счётчиков. */
const STATUS_META: Record<Exclude<ConvStatus, "RESOLVED">, { title: string; hint: string; tone: "mint" | "danger" | "acc" }> = {
  ACTIVE: { title: "Автономно", hint: "ИИ ведёт сам", tone: "mint" },
  HOLD: { title: "HOLD", hint: "нужен менеджер", tone: "danger" },
  HANDOFF: { title: "Менеджер", hint: "вы в диалоге", tone: "acc" },
};

/** Порядок в списке: сначала то, что требует человека. */
const STATUS_ORDER: ConvStatus[] = ["HOLD", "HANDOFF", "ACTIVE"];

interface NotifMeta { icon: typeof Bell; tone: string; label: string }

/**
 * Вид уведомления → иконка и подпись. Таблица открытая (Record, а не набор констант):
 * бэкенд может добавить новый тип — например `LEAD` из Telegram-бота-анкеты, — и лента
 * не должна из-за этого падать. Неизвестный тип показываем нейтрально.
 */
const NTYPE: Record<string, NotifMeta> = {
  DISPUTE: { icon: Swords, tone: "text-danger", label: "Спор" },
  SECURITY: { icon: ShieldAlert, tone: "text-amber", label: "Инъекция" },
  OVERRIDE_DUE: { icon: BellRing, tone: "text-acc2", label: "Нужно решение" },
  REMINDER: { icon: Bell, tone: "text-dim", label: "Напоминание" },
  LEAD: { icon: UserRoundPlus, tone: "text-mint", label: "Заявка из бота" },
  HANDOFF_REQUEST: { icon: Handshake, tone: "text-acc", label: "Клиент зовёт менеджера" },
};

const DEFAULT_META: NotifMeta = { icon: Bell, tone: "text-dim", label: "Уведомление" };

/**
 * Вид уведомления → иконка и подпись, с запасным вариантом для неизвестных типов.
 * Экспортируется не для интерфейса, а чтобы это правило проверялось тестом:
 * `npm run check:notif` (см. scripts/render-notif-meta.mjs).
 */
export function notificationMeta(type: string): NotifMeta {
  return NTYPE[type] ?? DEFAULT_META;
}

/** Порядок в ленте: сначала непрочитанные активные, затем прочитанные, затем закрытые. */
function feedWeight(n: NotificationDto): number {
  if (n.active && !n.read) return 0;
  if (n.active) return 1;
  return 2;
}

export default function DashboardPage() {
  const [list, setList] = useState<ConvListItem[]>([]);
  const [notif, setNotif] = useState<NotificationDto[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ConvDetail | null>(null);
  const [reply, setReply] = useState("");
  const [busy, setBusy] = useState(false);
  const [feedOnlyNew, setFeedOnlyNew] = useState(true);
  const [showSummaryDetails, setShowSummaryDetails] = useState(false);
  const [showDecisions, setShowDecisions] = useState(false);
  const [listVisible, setListVisible] = useState(true); // узкий экран: список ↔ чат
  const now = useNow(1000);
  const scrollRef = useRef<HTMLDivElement>(null);
  const pickedOnce = useRef(false);

  const refresh = async () => {
    try {
      const [c, n] = await Promise.all([api.conversations(), api.notifications()]);
      setList(c.conversations);
      setNotif(n.notifications);
    } catch { /* офлайн: оставляем прежние данные на экране */ }
  };
  usePolling(refresh, 3000);

  usePolling(async () => {
    if (selectedId) setDetail(await api.conversation(selectedId));
  }, 2200, [selectedId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [detail?.messages.length]);

  // Первый вход: сразу открываем HOLD-диалог (или любой первый) — менеджер не видит пустоту.
  useEffect(() => {
    if (pickedOnce.current || selectedId || list.length === 0) return;
    const hold = list.find((c) => c.status === "HOLD");
    const first = hold ?? list.find((c) => c.status !== "RESOLVED");
    if (first) {
      pickedOnce.current = true;
      setSelectedId(first.id);
    }
  }, [list, selectedId]);

  const order = useMemo(() => {
    const map = new Map<string, number>(STATUS_ORDER.map((s, i) => [s, i]));
    return [...list]
      .filter((c) => c.status !== "RESOLVED")
      .sort((a, b) => (map.get(a.status) ?? 9) - (map.get(b.status) ?? 9) || b.waitingMinutes - a.waitingMinutes);
  }, [list]);

  const counts = useMemo(() => ({
    ACTIVE: list.filter((c) => c.status === "ACTIVE").length,
    HOLD: list.filter((c) => c.status === "HOLD").length,
    HANDOFF: list.filter((c) => c.status === "HANDOFF").length,
  }), [list]);

  const feed = useMemo(
    () => [...notif].sort((a, b) => feedWeight(a) - feedWeight(b) || (a.createdAt < b.createdAt ? 1 : -1)),
    [notif],
  );
  const unread = feed.filter((n) => !n.read).length;
  const shownFeed = feedOnlyNew ? feed.filter((n) => !n.read) : feed;

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
      await refresh();
      if (selectedId) setDetail(await api.conversation(selectedId));
    } finally { setBusy(false); }
  };

  const openConversation = (id: string) => {
    if (!id) return;
    setSelectedId(id);
    setListVisible(false); // узкий экран: уходим из списка в чат
  };

  const select = (c: ConvListItem) => openConversation(c.id);

  return (
    <div>
      <PageHeader
        title="Чат менеджера"
        sub="Слева — лента уведомлений, остальное — переписка ИИ с клиентом. Клиент пишет в Telegram, вы видите диалог здесь и можете вмешаться в любой момент (Ст. 5, 9)."
        right={
          <>
            <Chip tone="mint">автономно: {counts.ACTIVE}</Chip>
            <Chip tone="danger" className={counts.HOLD ? "hold-pulse" : ""}>
              <Volume2 size={11} /> HOLD: {counts.HOLD}
            </Chip>
            <Chip tone="acc">менеджер: {counts.HANDOFF}</Chip>
          </>
        }
      />

      <div className="grid gap-4 xl:grid-cols-[286px_244px_minmax(0,1fr)]">
        {/* ---------------------------------------------------------------- лента уведомлений */}
        <Glass className={cx("flex max-h-[260px] flex-col overflow-hidden xl:max-h-[calc(100vh-170px)]", !listVisible && "hidden xl:flex")}>
          <div className="flex items-center justify-between gap-2 border-b border-edge px-3 py-2.5">
            <span className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.16em] text-dim">
              <Inbox size={12} /> Уведомления
            </span>
            <button
              onClick={() => setFeedOnlyNew((v) => !v)}
              className={cx("rounded-md px-2 py-0.5 text-[10px] transition",
                feedOnlyNew ? "bg-acc/15 text-txt" : "text-dim hover:text-txt")}
              title={feedOnlyNew ? "Показать все, включая прочитанные" : "Показать только новые"}
            >
              {feedOnlyNew ? `новые · ${unread}` : `все · ${feed.length}`}
            </button>
          </div>

          <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto p-2">
            {shownFeed.length === 0 && (
              <div className="px-2 py-6 text-center">
                <CircleCheckBig size={18} className="mx-auto text-mint/70" />
                <p className="mt-2 text-[11px] text-dim">
                  {feedOnlyNew ? "Всё разобрано — новых уведомлений нет." : "Уведомлений пока нет."}
                </p>
                {feedOnlyNew && feed.length > 0 && (
                  <button onClick={() => setFeedOnlyNew(false)} className="mt-1 text-[10.5px] text-acc2 underline">
                    показать прочитанные
                  </button>
                )}
              </div>
            )}

            {shownFeed.map((n) => {
              const meta = notificationMeta(n.type);
              const Icon = meta.icon;
              return (
                <div
                  key={n.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => (n.convId ? openConversation(n.convId) : void act(() => api.readNotification(n.id)))}
                  onKeyDown={(e) => { if (e.key === "Enter" && n.convId) openConversation(n.convId); }}
                  className={cx(
                    "cursor-pointer rounded-xl border px-2.5 py-2 transition",
                    n.active && !n.read
                      ? "border-danger/30 bg-danger/[0.06] hover:border-danger/60"
                      : "border-edge bg-white/[0.02] opacity-70 hover:opacity-100",
                    detail?.conversation.id === n.convId && "ring-1 ring-acc/50",
                  )}
                >
                  <div className="flex items-center gap-1.5">
                    <Icon size={12} className={meta.tone} />
                    <span className="text-[9.5px] font-semibold uppercase tracking-wider text-dim">{meta.label}</span>
                    <span className="mono ml-auto text-[9px] text-dim/70">{relTime(n.createdAt, now)}</span>
                  </div>
                  <p className="mt-1 line-clamp-3 text-[11px] leading-snug text-txt/85">{n.text}</p>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5">
                    {n.slaBreached && <Chip tone="danger" className="px-1.5 py-0 text-[9px]">SLA просрочен</Chip>}
                    {n.convId
                      ? <Chip tone="dim" className="px-1.5 py-0 text-[9px]">диалог {n.convId}</Chip>
                      : <Chip tone="mint" className="px-1.5 py-0 text-[9px]">карточка клиента</Chip>}
                    {!n.read && (
                      <button
                        onClick={(e) => { e.stopPropagation(); void act(() => api.readNotification(n.id)); }}
                        className="ml-auto inline-flex items-center gap-1 text-[9.5px] text-dim transition hover:text-txt"
                        title="Отметить прочитанным"
                      >
                        <CheckCheck size={11} /> прочитано
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </Glass>

        {/* ------------------------------------------------------------------------ диалоги */}
        <Glass className={cx("flex max-h-[300px] flex-col overflow-hidden xl:max-h-[calc(100vh-170px)]", !listVisible && "hidden xl:flex")}>
          <div className="flex items-center justify-between border-b border-edge px-3 py-2.5">
            <span className="text-[10px] uppercase tracking-[0.16em] text-dim">Диалоги ИИ с клиентами</span>
            <span className="mono text-[10px] text-dim">{order.length}</span>
          </div>
          <div className="min-h-0 flex-1 space-y-1 overflow-y-auto p-2">
            {order.length === 0 && (
              <p className="px-2 py-6 text-center text-[11px] text-dim">
                Диалогов нет. Как только клиент напишет боту в Telegram, переписка появится здесь.
              </p>
            )}
            {order.map((c) => {
              const meta = STATUS_META[c.status as Exclude<ConvStatus, "RESOLVED">] ?? STATUS_META.ACTIVE;
              return (
                <button
                  key={c.id}
                  onClick={() => select(c)}
                  className={cx(
                    "w-full rounded-xl border px-2.5 py-2 text-left transition",
                    selectedId === c.id ? "border-acc/60 bg-acc/10" : "border-transparent hover:bg-white/[0.04]",
                    c.status === "HOLD" && "hold-pulse",
                  )}
                >
                  <div className="flex items-center gap-1.5">
                    <span className={cx("size-1.5 shrink-0 rounded-full",
                      c.status === "HOLD" ? "bg-danger" : c.status === "HANDOFF" ? "bg-acc" : "bg-mint")} />
                    <span className="truncate text-[12px] font-medium">{c.client?.name ?? c.clientId}</span>
                    {c.suspicious && <ShieldAlert size={11} className="shrink-0 text-amber" />}
                    <span className="mono ml-auto shrink-0 text-[9px] text-dim/70">{relTime(c.lastAt, now)}</span>
                  </div>
                  <p className="mt-1 truncate text-[10.5px] text-dim">{c.lastMessage}</p>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5">
                    <span className={cx("text-[9.5px] font-semibold uppercase tracking-wider",
                      meta.tone === "danger" ? "text-danger" : meta.tone === "mint" ? "text-mint" : "text-acc")}>
                      {meta.title}
                    </span>
                    <CategoryChip category={c.category} withLabel={false} />
                    {c.status === "HOLD" && <span className="mono text-[9px] text-danger">ждёт {c.waitingMinutes} мин</span>}
                  </div>
                </button>
              );
            })}
          </div>
        </Glass>

        {/* ------------------------------------------------------------------- чат менеджера */}
        <Glass className={cx("flex min-h-[480px] flex-col overflow-hidden xl:max-h-[calc(100vh-170px)]", listVisible && "hidden xl:flex")}>
          {!detail ? (
            <div className="grid flex-1 place-items-center p-8 text-center">
              <div>
                <UserRoundCheck size={26} className="mx-auto text-acc/50" />
                <p className="mt-3 max-w-sm text-[12.5px] leading-relaxed text-dim">
                  Выберите диалог в списке. Здесь видно, что ИИ отвечает клиенту, что он решил и почему,
                  а вы в любой момент можете взять разговор на себя.
                </p>
              </div>
            </div>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2 border-b border-edge px-3.5 py-2.5">
                <button
                  onClick={() => setListVisible(true)}
                  className="rounded-md border border-edge px-2 py-1 text-[10.5px] text-dim transition hover:text-txt xl:hidden"
                >
                  ← диалоги
                </button>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13.5px] font-medium">{detail.client.name}</div>
                  <div className="truncate text-[10.5px] text-dim">
                    {[detail.client.segment, detail.client.contact].filter(Boolean).join(" · ")}
                  </div>
                </div>
                <CategoryChip category={detail.conversation.category} />
                <StatusChip status={detail.conversation.status} />
                {detail.conversation.status === "HOLD" && (
                  <Btn tone="mint" disabled={busy} onClick={() => void act(() => api.takeover(detail.conversation.id))}>
                    <Handshake size={13} /> Взять в работу
                  </Btn>
                )}
                {detail.conversation.status !== "RESOLVED" && (
                  <Btn tone="ghost" disabled={busy} onClick={() => void act(() => api.resolve(detail.conversation.id))}>
                    <CircleCheckBig size={13} /> Закрыть
                  </Btn>
                )}
              </div>

              <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3.5" ref={scrollRef}>
                {detail.handoff && detail.handoff.status !== "resolved" && (
                  <div className="edge-glow glass bg-acc/[0.05] p-3.5">
                    <button
                      onClick={() => setShowSummaryDetails((v) => !v)}
                      className="flex w-full items-center gap-2 text-left text-[10px] uppercase tracking-[0.18em] text-acc2"
                    >
                      <AlarmClockCheck size={12} /> Саммари ИИ · 30 секунд на чтение
                      <ChevronDown size={13} className={cx("ml-auto transition", showSummaryDetails && "rotate-180")} />
                    </button>
                    <p className="mt-2 text-[12.5px] font-medium leading-snug">{detail.handoff.summary.essence}</p>
                    <div className="mt-2 flex flex-wrap items-center gap-1.5">
                      <Chip tone={detail.handoff.summary.urgency === "high" ? "danger" : detail.handoff.summary.urgency === "medium" ? "amber" : "dim"}>
                        срочность: {detail.handoff.summary.urgency}
                      </Chip>
                      <Chip tone="mint" className="mono">рекомендация: {detail.handoff.summary.recommendedRange}</Chip>
                      {detail.handoff.summary.remedyOptions.map((r) => <Chip key={r} tone="sky">{r}</Chip>)}
                    </div>
                    {showSummaryDetails && (
                      <div className="mt-2.5 grid gap-3 border-t border-edge pt-2.5 md:grid-cols-2">
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
                    )}
                  </div>
                )}

                {detail.messages.map((m) => <Bubble key={m.id} m={m} compact />)}

                {detail.decisions.length > 0 && (
                  <div className="rounded-xl border border-edge bg-white/[0.02]">
                    <button
                      onClick={() => setShowDecisions((v) => !v)}
                      className="flex w-full items-center gap-2 px-3 py-2 text-left"
                    >
                      <span className="text-[9.5px] uppercase tracking-[0.16em] text-dim">
                        Решения ИИ · переопределение (Ст. 5)
                      </span>
                      <span className="mono text-[9.5px] text-dim">{detail.decisions.length}</span>
                      <ChevronDown size={13} className={cx("ml-auto text-dim transition", showDecisions && "rotate-180")} />
                    </button>
                    {showDecisions && (
                      <div className="space-y-2 px-3 pb-3">
                        {detail.decisions.slice(-4).map((d) => (
                          <DecisionRow key={d.id} d={d} onOverride={(reason) => void act(() => api.overrideDecision(d.id, reason))} busy={busy} />
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>

              {detail.conversation.status !== "RESOLVED" ? (
                <form
                  className="border-t border-edge p-3"
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (reply.trim()) void act(() => api.managerReply(detail.conversation.id, reply).then(() => setReply("")));
                  }}
                >
                  <div className="flex items-end gap-2">
                    <textarea
                      value={reply}
                      onChange={(e) => setReply(e.target.value)}
                      rows={2}
                      placeholder="Ваш ответ клиенту…"
                      className={inputCls}
                    />
                    <Btn disabled={busy || !reply.trim()} className="shrink-0">Отправить</Btn>
                  </div>
                  <p className="mt-1.5 text-[10px] text-dim">
                    {detail.conversation.status === "HOLD"
                      ? "Ответ сразу заберёт диалог у ИИ: клиент получит ваше сообщение, напоминания прекратятся."
                      : "Ответ уйдёт клиенту в его канал (Telegram). ИИ в этом диалоге замолчит."}
                  </p>
                </form>
              ) : (
                <div className="border-t border-edge px-3.5 py-3 text-[11.5px] text-dim">
                  Диалог закрыт. Уведомления и напоминания по нему остановлены.
                </div>
              )}
            </>
          )}
        </Glass>
      </div>

      {detail && (
        <div className="mt-3 flex items-center gap-2 text-[10px] text-dim/70">
          <Clock3 size={11} />
          обновление каждые 3 с · диалог {detail.conversation.id} · сообщений {detail.messages.length}
        </div>
      )}
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
