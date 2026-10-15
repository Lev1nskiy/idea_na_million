"use client";

import { motion } from "framer-motion";
import { CategoryChip, Chip, cx } from "@/components/ui";
import { fmtClock, fmtMs } from "@/lib/format";
import type { Message } from "@/lib/types";

/** Пузырь сообщения: клиент / ИИ / менеджер / системный. */
export function Bubble({ m, compact }: { m: Message; compact?: boolean }) {
  if (m.role === "system") {
    return (
      <div className="mx-auto max-w-[88%] rounded-lg border border-edge bg-white/[0.02] px-3 py-1.5 text-center text-[10.5px] text-dim">
        {m.text}
      </div>
    );
  }
  const isClient = m.role === "client";
  const isManager = m.role === "manager";
  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className={cx("flex", isClient ? "justify-end" : "justify-start")}>
      <div
        className={cx(
          "max-w-[88%] rounded-2xl px-3.5 py-2.5 leading-relaxed",
          compact ? "text-[12px]" : "text-[13px]",
          isClient && "rounded-br-md border border-acc/30 bg-acc/25",
          isManager && "rounded-bl-md border border-fuchsia-400/35 bg-fuchsia-400/10",
          !isClient && !isManager && "rounded-bl-md border border-edge bg-panel2",
        )}
      >
        <div className="mb-1 flex flex-wrap items-center gap-1.5">
          <span className="text-[9.5px] font-semibold uppercase tracking-wider text-dim">
            {isClient ? "Клиент" : isManager ? "Менеджер" : "СИНАПС"}
          </span>
          {m.phase && (
            <Chip tone={m.phase === "ACK" ? "mint" : m.phase === "HOLD" ? "danger" : "sky"} className="px-1.5 py-0 text-[9px]">
              {m.phase}{m.deterministic ? " · шаблон" : ""}
            </Chip>
          )}
          {m.latencyMs != null && (
            <span className={cx("mono text-[9px]", m.latencyMs < 100 ? "text-mint" : m.latencyMs < 5000 ? "text-acc2" : "text-amber")}>
              {fmtMs(m.latencyMs)}
            </span>
          )}
          {m.category && !isClient && <CategoryChip category={m.category} withLabel={false} />}
          <span className="mono ml-auto text-[9px] text-dim/70">{fmtClock(m.at)}</span>
        </div>
        <div className="whitespace-pre-wrap">{m.text}</div>
      </div>
    </motion.div>
  );
}
