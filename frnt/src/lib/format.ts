// Форматирование дат/времени и чисел (клиент).
export const fmtClock = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }) : "—";

export const fmtDate = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" }) : "—";

export function relTime(iso?: string | null, now: Date | null = null): string {
  if (!iso || !now) return "—";
  const diffMin = Math.max(0, Math.round((now.getTime() - new Date(iso).getTime()) / 60_000));
  if (diffMin < 1) return "только что";
  if (diffMin < 60) return `${diffMin} мин назад`;
  const h = Math.floor(diffMin / 60);
  if (h < 24) return `${h} ч ${diffMin % 60} мин назад`;
  return `${Math.floor(h / 24)} дн назад`;
}

export const fmtRub = (n?: number | null) =>
  n == null ? "—" : `${new Intl.NumberFormat("ru-RU").format(n)} ₽`;

export const fmtMs = (ms?: number | null) => {
  if (ms == null) return "—";
  if (ms < 1000) return `${Math.round(ms)} мс`;
  return `${(ms / 1000).toFixed(1)} с`;
};
