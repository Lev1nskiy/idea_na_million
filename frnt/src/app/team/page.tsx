"use client";

import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import {
  CircleCheckBig, KeyRound, ShieldCheck, ShieldQuestion, UserPlus, UserX,
} from "lucide-react";
import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { Btn, Chip, Glass, PageHeader, cx, inputCls } from "@/components/ui";
import type { Role, SiteUser } from "@/lib/types";

const ROLE_LABEL: Record<Role, string> = { admin: "администратор", manager: "менеджер" };

/** Раздел администратора: он заводит логины и пароли новым менеджерам и управляет доступами. */
export default function TeamPage() {
  const [me, setMe] = useState<SiteUser | null>(null);
  const [denied, setDenied] = useState(false);
  const [users, setUsers] = useState<SiteUser[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [draft, setDraft] = useState({ login: "", name: "", password: "", role: "manager" as Role });

  const load = async () => {
    try {
      const { users: rows } = await api.users();
      setUsers(rows);
    } catch {
      setDenied(true); // 403 = открыт раздел менеджером; интерфейс объясняет, что не так
    }
  };

  useEffect(() => {
    // Первичная загрузка данных с сервера: состояние обновляется после await,
    // поэтому каскада синхронных рендеров здесь нет (правило про setState в эффекте — не про этот случай).
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
    api.me().then(({ user }) => setMe(user)).catch(() => setDenied(true));
  }, []);

  const run = async (action: () => Promise<unknown>, success: string) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
      setNotice(success);
      await load();
      setTimeout(() => setNotice(null), 5000);
    } catch (err) {
      const raw = err instanceof Error ? err.message : "";
      try {
        setError((JSON.parse(raw) as { detail?: string }).detail ?? "Не получилось выполнить действие");
      } catch {
        setError("Не получилось выполнить действие");
      }
    } finally {
      setBusy(false);
    }
  };

  const create = (e: React.FormEvent) => {
    e.preventDefault();
    void run(
      async () => {
        await api.createUser(draft);
        setDraft({ login: "", name: "", password: "", role: "manager" });
      },
      `Сотрудник ${draft.login} создан — передайте ему логин и пароль`,
    );
  };

  if (denied) {
    return (
      <div className="mx-auto max-w-3xl pt-10">
        <Glass className="p-6">
          <div className="flex items-center gap-2 text-[13px] font-medium text-danger">
            <UserX size={15} /> Управление сотрудниками доступно только администратору
          </div>
          <p className="mt-2 text-[12px] leading-relaxed text-dim">
            Ваша роль: {me ? ROLE_LABEL[me.role] : "—"}. Логины и пароли выдаёт администратор;
            если вам нужен доступ к разделу, попросите его повысить роль.
          </p>
        </Glass>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        title="Сотрудники"
        sub="Логины и пароли для менеджеров: создаёт, меняет и отключает администратор"
        right={<Chip tone="acc"><ShieldCheck size={11} /> только администратор</Chip>}
      />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
        {/* список сотрудников */}
        <Glass className="overflow-hidden">
          <div className="flex items-center justify-between border-b border-edge px-4 py-3">
            <span className="text-[12px] font-medium">Команда ({users.length})</span>
            <span className="text-[10.5px] text-dim">смена пароля и роли сразу гасит активные сессии сотрудника</span>
          </div>
          <div className="divide-y divide-edge/60">
            {users.map((u, i) => (
              <motion.div
                key={u.id}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.04, duration: 0.25 }}
                className="grid gap-3 px-4 py-3 md:grid-cols-[1fr_auto] md:items-center"
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[13px] font-medium">{u.name}</span>
                    <Chip tone={u.role === "admin" ? "acc" : "sky"}>{ROLE_LABEL[u.role]}</Chip>
                    {!u.isActive && <Chip tone="danger">отключён</Chip>}
                    {me?.id === u.id && <Chip tone="mint">это вы</Chip>}
                  </div>
                  <div className="mono mt-1 text-[11px] text-dim">
                    {u.login} · создан {fmtDate(u.createdAt)}
                    {u.lastLoginAt ? ` · вход ${fmtDate(u.lastLoginAt)}` : " · ещё не входил"}
                  </div>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  <Btn
                    tone="ghost"
                    className="px-3 py-1.5 text-[11px]"
                    disabled={busy}
                    onClick={() => {
                      const password = window.prompt(`Новый пароль для ${u.login} (минимум 5 символов)`);
                      if (password) void run(() => api.patchUser(u.id, { password }), `Пароль для ${u.login} обновлён`);
                    }}
                  >
                    <KeyRound size={12} /> Сменить пароль
                  </Btn>
                  <Btn
                    tone="ghost"
                    className="px-3 py-1.5 text-[11px]"
                    disabled={busy || u.id === me?.id}
                    onClick={() =>
                      void run(
                        () => api.patchUser(u.id, { role: u.role === "admin" ? "manager" : "admin" }),
                        "Роль обновлена",
                      )
                    }
                  >
                    <ShieldQuestion size={12} /> {u.role === "admin" ? "Сделать менеджером" : "Сделать админом"}
                  </Btn>
                  <Btn
                    tone="ghost"
                    className={cx("px-3 py-1.5 text-[11px]", u.isActive ? "hover:border-danger/50 hover:text-danger" : "")}
                    disabled={busy || u.id === me?.id}
                    onClick={() =>
                      void run(
                        () => api.patchUser(u.id, { isActive: !u.isActive }),
                        u.isActive ? `${u.login} отключён` : `${u.login} снова активен`,
                      )
                    }
                  >
                    {u.isActive ? <><UserX size={12} /> Отключить</> : <><CircleCheckBig size={12} /> Включить</>}
                  </Btn>
                </div>
              </motion.div>
            ))}
          </div>
        </Glass>

        {/* новый сотрудник */}
        <Glass className="h-fit p-5">
          <div className="flex items-center gap-2 text-[12.5px] font-medium">
            <UserPlus size={14} className="text-acc" /> Новый сотрудник
          </div>
          <p className="mt-2 text-[11px] leading-relaxed text-dim">
            Задайте логин, пароль и роль — сотрудник войдёт на сайт по этим данным.
          </p>

          <form onSubmit={create} className="mt-4 space-y-3">
            <label className="block">
              <span className="mb-1.5 block text-[11px] text-dim">Логин (латиница, 3–32)</span>
              <input
                className={inputCls}
                value={draft.login}
                onChange={(e) => setDraft({ ...draft, login: e.target.value })}
                placeholder="ivan.p"
                required
              />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-[11px] text-dim">Имя сотрудника</span>
              <input
                className={inputCls}
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                placeholder="Иван Петров"
              />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-[11px] text-dim">Пароль (минимум 5 символов)</span>
              <input
                className={inputCls}
                value={draft.password}
                onChange={(e) => setDraft({ ...draft, password: e.target.value })}
                placeholder="print-2026"
                required
              />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-[11px] text-dim">Роль</span>
              <select
                className={inputCls}
                value={draft.role}
                onChange={(e) => setDraft({ ...draft, role: e.target.value as Role })}
              >
                <option value="manager">менеджер — диалоги и уведомления</option>
                <option value="admin">администратор — плюс политика и сотрудники</option>
              </select>
            </label>

            <Btn className="w-full justify-center px-4 py-2.5 text-[12.5px]" disabled={busy}>
              <UserPlus size={14} /> Создать сотрудника
            </Btn>
          </form>

          {error && (
            <div className="mt-3 rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-[11px] text-danger">{error}</div>
          )}
          {notice && (
            <div className="mt-3 rounded-lg border border-mint/40 bg-mint/10 px-3 py-2 text-[11px] text-mint">{notice}</div>
          )}
        </Glass>
      </div>
    </div>
  );
}
