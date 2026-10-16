// Проверка, что интерфейс действительно умеет отрисовать уведомление каждого вида.
//
// Зачем: вид уведомления приходит строкой с бэкенда, и раньше `NTYPE[type].icon` падал на новом
// типе (так дашборд сломался, когда из Telegram-бота пошли заявки с типом LEAD). Скрипт берёт
// функцию выбора иконки прямо из исходника страницы (`notificationMeta` в app/dashboard/page.tsx),
// подсовывает ей виды уведомлений и падает, если хоть один остался без иконки и подписи.
//
// Запуск: npm run check:notif  (вместе со статической сверкой типов)

import { createRequire } from "node:module";
import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync, readdirSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const FRONT = new URL("..", import.meta.url).pathname;
const out = mkdtempSync(join(tmpdir(), "notif-"));

try {
  // транспилируем только эту страницу: правила компонентов (иконки lucide) не мешают
  execFileSync(join(FRONT, "node_modules/.bin/tsc"), [
    join(FRONT, "src/app/dashboard/page.tsx"),
    "--jsx", "react-jsx", "--module", "commonjs", "--target", "es2020",
    "--moduleResolution", "node", "--skipLibCheck", "--esModuleInterop",
    "--allowSyntheticDefaultImports", "--outDir", out,
  ], { stdio: "pipe" });
} catch { /* ошибки типов не блокируют трансляцию — нам нужен только JS */ }

const findPage = (dir) => {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) { const found = findPage(p); if (found) return found; }
    else if (name === "page.js") return p;
  }
  return null;
};
const compiled = findPage(out);
if (!compiled) { console.error("не удалось скомпилировать страницу дашборда"); process.exit(1); }

const require = createRequire(join(FRONT, "x.js"));
const Module = require("module");
process.env.NODE_PATH = join(FRONT, "node_modules");   // react и lucide лежат рядом с проектом
Module._initPaths();

const original = Module._resolveFilename;
const stubs = {
  "@/lib/api": "api", "@/lib/hooks": "hooks", "@/lib/format": "format",
  "@/components/ui": "ui", "@/components/Bubble": "bubble", "@/lib/types": "types",
};
Module._resolveFilename = function (request, ...rest) {
  for (const [prefix, file] of Object.entries(stubs)) {
    if (request === prefix || request.startsWith(`${prefix}/`)) return join(out, `${file}-stub.js`);
  }
  return original.call(this, request, ...rest);
};
const stubCode = {
  api: "exports.api={}; exports.default={};",
  hooks: "exports.usePolling=()=>{}; exports.useNow=()=>0;",
  format: "exports.relTime=()=>'';",
  ui: "exports.Btn=()=>null;exports.Chip=()=>null;exports.Glass=()=>null;exports.PageHeader=()=>null;exports.StatusChip=()=>null;exports.CategoryChip=()=>null;exports.cx=(...a)=>a.filter(Boolean).join(' ');exports.inputCls='';",
  bubble: "exports.Bubble=()=>null;",
  types: "exports.default={};",
};
for (const [name, code] of Object.entries(stubCode)) writeFileSync(join(out, `${name}-stub.js`), code);

const { notificationMeta } = require(compiled);

/** Компонент ли это: lucide отдаёт forwardRef-объекты, а не функции. */
const isComponent = (value) => typeof value === "function" || (!!value && typeof value === "object");

// Проверяем все виды, которые сейчас шлёт бэкенд, плюс неизвестный — на будущее.
const TYPES = ["DISPUTE", "SECURITY", "OVERRIDE_DUE", "REMINDER", "LEAD", "СОВСЕМ_НОВЫЙ_ТИП"];
let bad = 0;
for (const type of TYPES) {
  const meta = notificationMeta(type);
  const ok = !!meta && isComponent(meta.icon) && !!meta.label && !!meta.tone;
  console.log(`${ok ? "✓" : "✗"} ${type.padEnd(16)} → ${meta?.label ?? "нет описания"}`);
  if (!ok) bad++;
}

if (bad) {
  console.error("\nВИД УВЕДОМЛЕНИЯ БЕЗ ИКОНКИ: добавьте его в NTYPE (app/dashboard/page.tsx)");
  process.exit(1);
}
console.log("\nок: любой вид уведомления отрисуется, лента не падает");
