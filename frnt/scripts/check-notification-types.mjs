// Проверка связи «бэкенд → интерфейс» для уведомлений.
//
// Зачем: в ленте дашборда вид уведомления берётся из таблицы NTYPE по строке `type`, которую
// присылает бэкенд. Стоит добавить на сервере новый тип (так появился LEAD из Telegram-бота) —
// и `NTYPE[type]` оказывается undefined, а `meta.icon` роняет всю страницу дашборда в проде.
// Скрипт сверяет три источника и падает, если они разошлись:
//   1) union NotificationType в src/lib/types.ts  — что интерфейс ожидает;
//   2) ключи NTYPE в src/app/dashboard/page.tsx   — что интерфейс умеет рисовать;
//   3) типы, которые реально пишет бэкенд (значения m.Notification(type=...) в app/).
//
// Запуск: npm run check:notif   (входит в npm run lint-цепочку CI, если она появится)

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const FRONT = new URL("..", import.meta.url).pathname;
const BACKEND = join(FRONT, "..", "ai-native-srm");

const read = (p) => readFileSync(p, "utf8");

/** Типы, объявленные в union NotificationType. */
function declaredTypes() {
  const src = read(join(FRONT, "src/lib/types.ts"));
  const match = src.match(/export type NotificationType\s*=([^;]+);/s);
  if (!match) throw new Error("не нашёл NotificationType в src/lib/types.ts");
  return match[1].match(/"([A-Z_]+)"/g)?.map((s) => s.replaceAll('"', "")) ?? [];
}

/** Типы, для которых в дашборде есть иконка и подпись (плюс запасной вариант DEFAULT_META). */
function renderedTypes() {
  const src = read(join(FRONT, "src/app/dashboard/page.tsx"));
  const block = src.match(/const NTYPE[^=]*=\s*{([\s\S]*?)\n};/);
  if (!block) throw new Error("не нашёл таблицу NTYPE в src/app/dashboard/page.tsx");
  const keys = [...block[1].matchAll(/^\s{2}([A-Z_]+):/gm)].map((m) => m[1]);
  // функция-обёртка notificationMeta(...) скрывает запись таблицы: считаем запасной вариант
  // на месте, если он есть и внутри обёртки, и в самой ленте
  const hasFallback = /return\s+NTYPE\[type\]\s*\?\?\s*DEFAULT_META/.test(src)
    && /notificationMeta\(n\.type\)/.test(src);
  return { keys, hasFallback };
}

/** Типы, которые пишет бэкенд: Notification(type="…") или константа-литерал в python-коде. */
function backendTypes() {
  const files = [];
  const walk = (dir) => {
    for (const name of readdirSync(dir)) {
      if (["__pycache__", ".venv", "node_modules", ".next"].includes(name)) continue;
      const path = join(dir, name);
      if (statSync(path).isDirectory()) { walk(path); continue; }
      if (name.endsWith(".py")) files.push(read(path));
    }
  };
  walk(BACKEND);

  // Константы вида NOTIFY_TYPE = "LEAD" собираем по файлам: сначала ищем в том же файле,
  // где стоит Notification(type=…), и только потом — по всему бэкенду. Иначе одинаковое
  // имя константы в двух местах (NOTIFY_TYPE есть и у заявок, и у бота) подменяло бы тип.
  const constantsOf = (src) =>
    new Map([...src.matchAll(/^([A-Z][A-Z0-9_]*)\s*=\s*"([A-Z_]+)"/gm)].map((m) => [m[1], m[2]]));

  const found = new Set();
  for (const src of files) {
    const local = constantsOf(src);
    for (const m of src.matchAll(/Notification\(\s*type\s*=\s*([A-Za-z_][A-Za-z0-9_]*)/g)) {
      const raw = m[1];
      if (local.has(raw)) found.add(local.get(raw));
      else if (/^[A-Z_]+$/.test(raw)) found.add(raw);
    }
  }
  return [...found].sort();
}

const declared = declaredTypes();
const { keys, hasFallback } = renderedTypes();
const backend = backendTypes();

const problems = [];
for (const type of declared) if (!keys.includes(type)) problems.push(`интерфейс объявляет «${type}», но в NTYPE его нет — лента упадёт`);
for (const type of backend) if (!keys.includes(type)) problems.push(`бэкенд пишет «${type}», а в NTYPE его нет — лента упадёт`);
for (const type of keys) if (!declared.includes(type)) problems.push(`в NTYPE есть «${type}», но он не объявлен в NotificationType`);
if (!hasFallback) problems.push("нет запасного варианта (NTYPE[type] ?? DEFAULT_META) — любой новый тип уронит дашборд");

console.log(`типы бэкенда:      ${backend.join(", ") || "—"}`);
console.log(`объявлено в типах: ${declared.join(", ") || "—"}`);
console.log(`описано в NTYPE:   ${keys.join(", ") || "—"}${hasFallback ? " + запасной вариант" : ""}`);

if (problems.length) {
  console.error("\nРАСХОЖДЕНИЕ:");
  for (const p of problems) console.error("  • " + p);
  process.exit(1);
}
console.log("\nок: каждый тип уведомления из бэкенда интерфейс умеет показать");
