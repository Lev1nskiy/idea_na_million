"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { BrainCircuit, KeyRound, LogIn, ShieldCheck, User } from "lucide-react";
import { api } from "@/lib/api";
import { Btn, Glass, inputCls } from "@/components/ui";

/** Вход сотрудника: логин и пароль выдаёт администратор (раздел «Сотрудники»). */
export default function LoginPage() {
  const router = useRouter();
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.login(login.trim(), password);
      const next = new URLSearchParams(window.location.search).get("next");
      router.replace(next && next.startsWith("/") ? next : "/");
      router.refresh();
    } catch (err) {
      // текст ошибки приходит из API: «Неверный логин или пароль» / «Учётка отключена администратором»
      const raw = err instanceof Error ? err.message : "";
      let detail = "Не удалось войти. Попробуйте ещё раз.";
      try {
        detail = (JSON.parse(raw) as { detail?: string }).detail ?? detail;
      } catch { /* не JSON — оставляем общий текст */ }
      setError(detail);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid min-h-[82vh] place-items-center">
      <div className="w-full max-w-[420px]">
        <div className="mb-5 flex items-center gap-3">
          <div className="grid size-10 place-items-center rounded-xl border border-acc/40 bg-gradient-to-br from-acc/30 to-acc2/10 text-acc">
            <BrainCircuit size={20} strokeWidth={1.8} />
          </div>
          <div>
            <div className="font-display text-[15px] font-semibold tracking-wide">ЛОСЬ</div>
            <div className="text-[10px] uppercase tracking-[0.22em] text-dim">ai-native crm</div>
          </div>
        </div>

        <Glass className="p-6">
          <div className="flex items-center gap-2 text-[13px] font-medium">
            <ShieldCheck size={15} className="text-mint" />
            Вход для сотрудников
          </div>
          <p className="mt-2 text-[11.5px] leading-relaxed text-dim">
            Рабочее место менеджера и администратора. Клиентский чат смотрит клиент — сотруднику
            нужны логин и пароль, которые выдаёт администратор.
          </p>

          <form onSubmit={submit} className="mt-5 space-y-3">
            <label className="block">
              <span className="mb-1.5 flex items-center gap-1.5 text-[11px] text-dim"><User size={12} /> Логин</span>
              <input
                className={inputCls}
                value={login}
                onChange={(e) => setLogin(e.target.value)}
                autoComplete="username"
                autoFocus
                required
              />
            </label>
            <label className="block">
              <span className="mb-1.5 flex items-center gap-1.5 text-[11px] text-dim"><KeyRound size={12} /> Пароль</span>
              <input
                className={inputCls}
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
            </label>

            {error && (
              <div className="rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-[11.5px] text-danger">
                {error}
              </div>
            )}

            <Btn className="w-full justify-center px-4 py-2.5 text-[13px]" disabled={busy}>
              <LogIn size={14} />
              {busy ? "Проверяем…" : "Войти"}
            </Btn>
          </form>

          <div className="mt-4 rounded-lg bg-white/[0.03] px-3 py-2.5 text-[11px] leading-relaxed text-dim">
            Демо-доступ: <span className="mono text-acc2">admin / admin</span> — администратор
            (политика и сотрудники), <span className="mono text-acc2">manager / manager</span> — менеджер
            (диалоги и уведомления).
          </div>
        </Glass>
      </div>
    </div>
  );
}
