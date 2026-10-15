"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import {
  Activity, BrainCircuit, LayoutGrid, MessagesSquare, RotateCcw, ShieldAlert, SlidersHorizontal,
} from "lucide-react";

const ITEMS = [
  { href: "/", label: "Обзор", icon: LayoutGrid },
  { href: "/chat", label: "Чат клиента", icon: MessagesSquare },
  { href: "/dashboard", label: "Дашборд менеджера", icon: BrainCircuit },
  { href: "/policy", label: "Политика", icon: SlidersHorizontal },
  { href: "/metrics", label: "Безопасность · метрики", icon: Activity },
];

interface NotificationDto { id: string; active: boolean; read: boolean; }

export function Nav() {
  const pathname = usePathname();
  const [unread, setUnread] = useState(0);
  const [policyVersion, setPolicyVersion] = useState<number | null>(null);
  const [resetting, setResetting] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const [n, p] = await Promise.all([
          fetch("/api/notifications").then((r) => r.json()),
          fetch("/api/policy").then((r) => r.json()),
        ]);
        if (!alive) return;
        setUnread((n.notifications as NotificationDto[]).filter((x) => x.active && !x.read).length);
        setPolicyVersion(p.policy.version);
      } catch { /* сеть недоступна — пропускаем тик */ }
    };
    void load();
    const t = setInterval(load, 4000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  const reset = async () => {
    setResetting(true);
    await fetch("/api/demo/reset", { method: "POST" });
    window.location.reload();
  };

  return (
    <aside className="sticky top-0 z-40 hidden h-screen w-[248px] shrink-0 flex-col border-r border-edge bg-panel/70 backdrop-blur-xl md:flex">
      <Link href="/" className="group flex items-center gap-3 px-5 pb-5 pt-6">
        <div className="grid size-10 place-items-center rounded-xl border border-acc/40 bg-gradient-to-br from-acc/30 to-acc2/10 text-acc shadow-[0_0_24px_-6px_rgba(124,108,255,.7)] transition group-hover:scale-105">
          <BrainCircuit size={20} strokeWidth={1.8} />
        </div>
        <div>
          <div className="font-display text-[15px] font-semibold tracking-wide">СИНАПС</div>
          <div className="text-[10px] uppercase tracking-[0.22em] text-dim">ai-native crm</div>
        </div>
      </Link>

      <nav className="flex-1 space-y-1 px-3">
        {ITEMS.map((it) => {
          const active = pathname === it.href;
          const Icon = it.icon;
          return (
            <Link
              key={it.href}
              href={it.href}
              className={`group relative flex items-center gap-3 rounded-xl px-3 py-2.5 text-[13px] transition ${
                active ? "bg-acc/15 text-txt" : "text-dim hover:bg-white/[0.04] hover:text-txt"
              }`}
            >
              {active && <span className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-full bg-acc shadow-[0_0_12px_rgba(124,108,255,.9)]" />}
              <Icon size={17} strokeWidth={1.8} className={active ? "text-acc" : "text-dim group-hover:text-acc2"} />
              <span className="flex-1">{it.label}</span>
              {it.href === "/dashboard" && unread > 0 && (
                <span className="grid min-w-5 place-items-center rounded-full bg-danger px-1.5 py-0.5 text-[10px] font-semibold text-white">
                  {unread}
                </span>
              )}
              {it.href === "/metrics" && unread === 0 && <ShieldAlert size={13} className="text-dim/50" />}
            </Link>
          );
        })}
      </nav>

      <div className="space-y-2.5 border-t border-edge p-4">
        <div className="flex items-center justify-between rounded-lg bg-white/[0.03] px-3 py-2 text-[11px] text-dim">
          <span>Policy Matrix</span>
          <span className="mono font-semibold text-mint">v{policyVersion ?? "…"}</span>
        </div>
        <button
          onClick={reset}
          disabled={resetting}
          className="flex w-full items-center justify-center gap-2 rounded-lg border border-edge px-3 py-2 text-[11px] text-dim transition hover:border-danger/50 hover:text-danger disabled:opacity-50"
        >
          <RotateCcw size={12} className={resetting ? "animate-spin" : ""} />
          Сбросить демо-данные
        </button>
        <div className="px-1 text-[10px] leading-relaxed text-dim/70">
          LLM: GigaChat → YandexGPT → <span className="text-acc2">MockLLM</span>
        </div>
      </div>
    </aside>
  );
}
