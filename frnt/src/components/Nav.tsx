"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  Activity, BrainCircuit, LayoutGrid, LogOut, MessagesSquare, RotateCcw, ShieldAlert,
  SlidersHorizontal, Users,
} from "lucide-react";
import { api } from "@/lib/api";
import type { SiteUser } from "@/lib/types";

const BASE_ITEMS = [
  { href: "/", label: "Обзор", icon: LayoutGrid },
  { href: "/chat", label: "Чат клиента", icon: MessagesSquare },
  { href: "/dashboard", label: "Дашборд менеджера", icon: BrainCircuit },
  { href: "/metrics", label: "Безопасность · метрики", icon: Activity },
];

const ADMIN_ITEMS = [
  { href: "/policy", label: "Политика", icon: SlidersHorizontal },
  { href: "/team", label: "Сотрудники", icon: Users },
];

interface NotificationDto { id: string; active: boolean; read: boolean; }

const ROLE_LABEL: Record<string, string> = { admin: "администратор", manager: "менеджер" };

export function Nav() {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<SiteUser | null>(null);
  const [unread, setUnread] = useState(0);
  const [policyVersion, setPolicyVersion] = useState<number | null>(null);
  const [resetting, setResetting] = useState(false);

  // На экране входа сайдбар не нужен — он и не должен показывать рабочие разделы.
  const onLoginPage = pathname === "/login";

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      router.replace("/login");
      router.refresh();
    }
  }, [router]);

  useEffect(() => {
    if (onLoginPage) return;
    let alive = true;
    api.me()
      .then(({ user: me }) => { if (alive) setUser(me); })
      .catch(() => { if (alive) router.replace(`/login?next=${encodeURIComponent(pathname)}`); });
    return () => { alive = false; };
  }, [onLoginPage, pathname, router]);

  useEffect(() => {
    if (onLoginPage) return;
    let alive = true;
    const load = async () => {
      try {
        const n = await api.notifications();
        if (!alive) return;
        setUnread(n.notifications.filter((x) => x.active && !x.read).length);
      } catch { /* нет сети или нет сессии — пропускаем тик */ }
      if (user?.role === "admin") {
        try {
          const { policy } = await api.policy();
          if (alive) setPolicyVersion(policy.version);
        } catch { /* политика недоступна — не показываем версию */ }
      }
    };
    void load();
    const t = setInterval(load, 4000);
    return () => { alive = false; clearInterval(t); };
  }, [onLoginPage, user?.role]);

  const reset = async () => {
    setResetting(true);
    await api.demoReset();
    window.location.reload();
  };

  if (onLoginPage) return null;

  const items = user?.role === "admin" ? [...BASE_ITEMS, ...ADMIN_ITEMS] : BASE_ITEMS;

  return (
    <aside className="sticky top-0 z-40 hidden h-screen w-[248px] shrink-0 flex-col border-r border-edge bg-panel/70 backdrop-blur-xl md:flex">
      <Link href="/" className="group flex items-center gap-3 px-5 pb-5 pt-6">
        <div className="grid size-10 place-items-center rounded-xl border border-acc/40 bg-gradient-to-br from-acc/30 to-acc2/10 text-acc shadow-[0_0_24px_-6px_rgba(124,108,255,.7)] transition group-hover:scale-105">
          <BrainCircuit size={20} strokeWidth={1.8} />
        </div>
        <div>
          <div className="font-display text-[15px] font-semibold tracking-wide">ЛОСЬ</div>
          <div className="text-[10px] uppercase tracking-[0.22em] text-dim">ai-native crm</div>
        </div>
      </Link>

      <nav className="flex-1 space-y-1 px-3">
        {items.map((it) => {
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
        {/* сотрудник и роль: интерфейс знает, что можно показывать, а что запрещено на бэкенде */}
        <div className="flex items-center gap-2.5 rounded-lg bg-white/[0.03] px-3 py-2">
          <div className="grid size-7 shrink-0 place-items-center rounded-full border border-edge text-[11px] font-semibold text-acc2">
            {(user?.name ?? "—").slice(0, 1)}
          </div>
          <div className="min-w-0 flex-1">
            <div className="truncate text-[11.5px] text-txt/90">{user?.name ?? "…"}</div>
            <div className="text-[10px] text-dim">{user ? ROLE_LABEL[user.role] : ""}</div>
          </div>
          <button
            onClick={logout}
            title="Выйти"
            className="grid size-7 place-items-center rounded-md text-dim transition hover:bg-white/[0.06] hover:text-danger"
          >
            <LogOut size={13} />
          </button>
        </div>

        {user?.role === "admin" && (
          <>
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
          </>
        )}

        <div className="px-1 text-[10px] leading-relaxed text-dim/70">
          LLM: GigaChat → YandexGPT → <span className="text-acc2">MockLLM</span>
        </div>
      </div>
    </aside>
  );
}
