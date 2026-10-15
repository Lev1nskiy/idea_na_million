"use client";

import { useEffect, useRef, useState } from "react";

/** Поллинг с заданным интервалом (аналог st_autorefresh 2с из плана). */
export function usePolling(fn: () => void | Promise<void>, ms: number, deps: unknown[] = []) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    void ref.current();
    const t = setInterval(() => void ref.current(), ms);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}

/** Текущее время только на клиенте (избегаем hydration-расхождений). */
export function useNow(tickMs = 1000): Date | null {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    setNow(new Date());
    const t = setInterval(() => setNow(new Date()), tickMs);
    return () => clearInterval(t);
  }, [tickMs]);
  return now;
}
