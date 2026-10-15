// Приватность (Ст. 4): ПДн не попадают в открытые логи / events / инциденты.
// Аналог core/privacy.py — mask_pii + text_hash.

const RE_PHONE = /(\+7|8)[\s(]?\d{3}[\s)]?[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}/g;
const RE_EMAIL = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g;
const RE_SERIAL = /\bSN-\d{3,}\b/g;
const RE_MONEY = /\d[\d\s]{2,}\s?(₽|руб\.?|р\.)/g;
// NB: \b в JS не работает с кириллицей — используем lookaround-границы.
// Исключаем названия организаций: внутри кавычек «…» или после корпоративных префиксов (ООО, АО, ИП и т.д.)
const RE_FULLNAME = /(?<![А-Яа-яЁё])[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ][а-яё]{2,}(?:\s+[А-ЯЁ][а-яё]{2,})?(?![а-яё])/g;
const RE_ORG_CONTEXT = /(?:ООО|АО|ЗАО|ПАО|ИП|НКО|ФГУП)\s*«[^»]*»/g;

/** Собираем набор подстрок, являющихся названиями организаций — их маскировать нельзя. */
function extractOrgNames(text: string): Set<string> {
  const names = new Set<string>();
  // названия в кавычках «…» после корпоративных префиксов
  for (const m of text.matchAll(RE_ORG_CONTEXT)) {
    const inner = m[0].match(/«([^»]+)»/);
    if (inner) names.add(inner[1]);
  }
  // любые подстроки внутри «…» (названия компаний в уведомлениях/логах)
  for (const m of text.matchAll(/«([^»]+)»/g)) {
    names.add(m[1]);
  }
  return names;
}

/** Маскирует телефоны, email, серийники, денежные суммы и паттерны ФИО.
 *  Названия организаций (внутри «…» или после ООО/АО/ИП) не маскируются. */
export function maskPii(text: string): string {
  const orgNames = extractOrgNames(text);
  return text
    .replace(RE_PHONE, "[телефон]")
    .replace(RE_EMAIL, "[email]")
    .replace(RE_SERIAL, "[серийник]")
    .replace(RE_MONEY, "[сумма]")
    .replace(RE_FULLNAME, (match) => {
      // если совпадение является частью названия организации — пропускаем
      for (const org of orgNames) {
        if (org.includes(match)) return match;
      }
      return match.split(" ").length >= 2 && match.length > 6 ? "[ФИО]" : match;
    });
}

/** Некриптографический быстрый хэш для инцидентов (djb2 → hex). */
export function textHash(text: string): string {
  let h1 = 5381;
  let h2 = 52711;
  for (let i = 0; i < text.length; i++) {
    const c = text.charCodeAt(i);
    h1 = (h1 * 33) ^ c;
    h2 = (h2 * 33) ^ c;
  }
  return ((h1 >>> 0).toString(16) + (h2 >>> 0).toString(16)).padStart(16, "0");
}
