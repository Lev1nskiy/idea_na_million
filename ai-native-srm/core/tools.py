"""Tools: единственный источник фактов (цены, скидки, сроки, гарантия, доставка, КП).

Правила:
- ВСЕ функции принимают client_id; данные других клиентов физически не возвращаются (Ст. 4).
- Скидки, компенсации, срок замены и SLA считаются ТОЛЬКО через core.policy (Ст. 2).
- Результаты — простые dict: они идут в блок FACTS и в белый список чисел (Ст. 2).
"""
from __future__ import annotations

import json
import re
from datetime import date
from functools import lru_cache
from typing import Any

from core import policy as policy_calc
from core.config import DATA_DIR
from core.schemas import Policy

# ---------------------------------------------------------------------------
# Загрузка статичных данных
# ---------------------------------------------------------------------------


def _read_json(name: str) -> Any:
    with open(DATA_DIR / name, encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _catalog() -> dict[str, dict[str, Any]]:
    return {item["sku"]: item for item in _read_json("catalog.json")["items"]}


@lru_cache(maxsize=1)
def _clients() -> dict[int, dict[str, Any]]:
    return {c["client_id"]: c for c in _read_json("clients_history.json")["clients"]}


@lru_cache(maxsize=1)
def _quote_templates() -> list[dict[str, Any]]:
    return _read_json("quote_templates.json")["templates"]


def reload_data() -> None:
    """Сбрасывает кэш статичных файлов (для тестов)."""
    _catalog.cache_clear()
    _clients.cache_clear()
    _quote_templates.cache_clear()


def catalog_items(limit: int = 10) -> list[dict[str, Any]]:
    """Позиции каталога для витрины (бот, экран «Каталог и цены»): sku, название, цена, наличие."""
    items = _read_json("catalog.json")["items"]
    return [
        {
            "sku": item["sku"],
            "name": item["name"],
            "price": item["price"],
            "stock": item.get("stock", 0),
            "lead_time_days": item.get("lead_time_days"),
            "warranty_months": item.get("warranty_months"),
        }
        for item in items[:limit]
    ]


def get_client(client_id: int) -> dict[str, Any] | None:
    return _clients().get(client_id)


# ---------------------------------------------------------------------------
# Каталог
# ---------------------------------------------------------------------------
_STOP = {
    "сколько", "стоит", "есть", "ли", "в", "на", "и", "для", "что", "какой", "какая", "какие",
    "цена", "цену", "цены", "стоимость", "наличии", "наличие", "нужен", "нужна", "нужно", "хочу",
    "купить", "заказать", "подскажите", "пожалуйста", "здравствуйте", "добрый", "день", "у", "вас",
    "вам", "мне", "по", "с", "а", "как", "когда", "срок", "сроки", "можно", "модель", "шт", "штук",
    "дайте", "дадите", "скидку", "скидка", "скидки", "ли", "это", "или", "не", "от", "до",
}
_CATEGORY_WORDS = {
    "mfu": "мфу", "printer": "принтер", "scanner": "сканер",
    "consumable": "картридж тонер расходник расходные бумага барабан",
    "service": "сервис обслуживание поддержка",
}


def _haystack(item: dict[str, Any]) -> str:
    return " ".join(
        [item["sku"], item["name"], _CATEGORY_WORDS.get(item["category"], ""), item.get("description", "")]
    ).lower()


def _search(query: str) -> list[tuple[int, dict[str, Any]]]:
    """Нечёткий поиск по каталогу: [(score, item)] по убыванию."""
    tokens = [t for t in re.findall(r"[a-zа-яё0-9\-]+", query.lower()) if len(t) >= 2 and t not in _STOP]
    ranked: list[tuple[int, dict[str, Any]]] = []
    for item in _catalog().values():
        hay = _haystack(item)
        sku = item["sku"].lower()
        score = 0
        for token in tokens:
            if token == sku:
                score += 10
            elif re.search(r"\d", token) and token.replace("-", "") in sku.replace("-", ""):
                score += 5  # номер модели без префикса: «m4200»
            elif (token[:5] if len(token) > 5 else token) in hay:
                score += 1
        if score:
            ranked.append((score, item))
    ranked.sort(key=lambda p: (-p[0], -(p[1].get("stock") or 0)))
    return ranked


def item_facts(item: dict[str, Any]) -> dict[str, Any]:
    """Компактное представление позиции каталога для блока FACTS."""
    return {
        "sku": item["sku"],
        "name": item["name"],
        "price": item["price"],
        "stock_qty": item.get("stock"),  # None — услуга
        "lead_time_days": item.get("lead_time_days"),
        "warranty_months": item.get("warranty_months"),
        "compat": item.get("compat", []),
        "specs": item.get("specs", {}),
    }


_EXACT_MATCH_SCORE = 5  # совпадение с SKU или номером модели (см. _search)


def catalog_lookup(client_id: int, query: str, limit: int = 3) -> list[dict[str, Any]]:
    """Позиции каталога по запросу. Каталог публичный; client_id — для единого контракта tools.

    Порог релевантности: если в запросе есть модель/SKU, «соседи» по номеру
    (D-4200 и T-4800 для PX-M4200) в ответ не попадают, а не выглядят альтернативами.
    """
    ranked = _search(query)
    if not ranked:
        return []
    best = ranked[0][0]
    threshold = max(_EXACT_MATCH_SCORE, best - 1) if best >= _EXACT_MATCH_SCORE else 1
    return [item_facts(item) for score, item in ranked if score >= threshold][:limit]


# ---------------------------------------------------------------------------
# Скидки (Ст. 2: только через Policy)
# ---------------------------------------------------------------------------
def calc_discount(
    client_id: int,
    policy: Policy,
    *,
    query: str | None = None,
    qty: int | None = None,
    budget: float | None = None,
    is_bundle: bool = False,
) -> dict[str, Any]:
    """Максимально допустимая скидка по актуальной Policy для заказа клиента.

    order_sum определяется по каталогу (позиция × qty) или по заявленному бюджету.
    Если позицию нельзя определить однозначно, allowed_pct = None (нужны уточнения).
    """
    client = get_client(client_id)
    status = client["status"] if client else "new"
    quantity = qty or 1
    item: dict[str, Any] | None = None
    order_sum: float | None = None

    if query:
        ranked = _search(query)
        if ranked and (len(ranked) == 1 or ranked[0][0] > ranked[1][0]):
            item = ranked[0][1]
            order_sum = item["price"] * quantity
    if order_sum is None and budget:
        order_sum = float(budget)

    allowed = (
        None
        if order_sum is None
        else policy_calc.calc_allowed_discount(policy, quantity, order_sum, status, is_bundle)
    )
    return {
        "order_sum": order_sum,
        "allowed_pct": allowed,
        "qty": quantity,
        "sku": item["sku"] if item else None,
        "product": item["name"] if item else None,
        "unit_price": item["price"] if item else None,
        "client_status": status,
    }


# ---------------------------------------------------------------------------
# Гарантия (G)
# ---------------------------------------------------------------------------
def _fmt_date(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%d.%m.%Y")


def _norm_serial(value: str) -> str:
    return re.sub(r"[\s]", "", value).upper()


def check_warranty(
    client_id: int,
    policy: Policy,
    *,
    serial: str | None = None,
    product: str | None = None,
    order_id: str | None = None,
) -> dict[str, Any]:
    """Ищет подтверждающий заказ ТОЛЬКО среди заказов этого клиента (Ст. 4, 5)."""
    client = get_client(client_id)
    orders = client["orders"] if client else []
    found_order: dict[str, Any] | None = None
    found_line: dict[str, Any] | None = None
    found_serial: str | None = None

    for order in orders:
        if order_id and order["order_id"].upper() == order_id.strip().upper():
            found_order, found_line = order, order["items"][0]
        for line in order["items"]:
            if serial and any(_norm_serial(s) == _norm_serial(serial) for s in line.get("serials", [])):
                found_order, found_line, found_serial = order, line, _norm_serial(serial)
    if found_order is None and product:
        ordered_skus = {line["sku"] for order in orders for line in order["items"]}
        for _, item in _search(product):
            if item["sku"] in ordered_skus:
                for order in orders:
                    for line in order["items"]:
                        if line["sku"] == item["sku"] and found_order is None:
                            found_order, found_line = order, line
                break
    if found_order is None and not (serial or order_id or product):
        lines = [(o, line) for o in orders for line in o["items"]]
        if len(lines) == 1:  # единственная покупка клиента — однозначно
            found_order, found_line = lines[0]

    if found_order is None or found_line is None:
        return {"order_found": False}
    item = _catalog().get(found_line["sku"], {})
    return {
        "order_found": True,
        "order_id": found_order["order_id"],
        "order_date": _fmt_date(found_order["date"]),
        "sku": found_line["sku"],
        "product": item.get("name", found_line["sku"]),
        "serial": found_serial,
        "replace_hours": policy.warranty.replace_hours,
    }


# ---------------------------------------------------------------------------
# Доставка (H)
# ---------------------------------------------------------------------------
_STATUS_TEXT = {"delivered": "доставлен", "in_transit": "в пути", "processing": "в обработке"}


def delivery_status(client_id: int, policy: Policy, *, order_id: str | None = None) -> dict[str, Any]:
    """Статус заказа клиента + компенсация и remedy из Policy (Ст. 5)."""
    client = get_client(client_id)
    orders = client["orders"] if client else []
    order: dict[str, Any] | None = None
    if order_id:
        order = next((o for o in orders if o["order_id"].upper() == order_id.strip().upper()), None)
    if order is None and orders:
        open_orders = [o for o in orders if o["status"] != "delivered"]
        order = (open_orders or orders)[-1]
    if order is None:
        return {"order_found": False}

    delay = int(order.get("delay_days", 0))
    comp = policy_calc.calc_delivery_compensation(policy, delay, order["total"])
    return {
        "order_found": True,
        "order_id": order["order_id"],
        "status": order["status"],
        "status_text": _STATUS_TEXT.get(order["status"], order["status"]),
        "delay_days": delay,
        "order_sum": order["total"],
        "compensation_pct": comp.pct,
        "compensation_amount": comp.amount,
    }


# ---------------------------------------------------------------------------
# КП по шаблону (D)
# ---------------------------------------------------------------------------
def find_quote_template(client_id: int, people_count: int) -> dict[str, Any] | None:
    for template in _quote_templates():
        if template["people_min"] <= people_count <= template["people_max"]:
            return template
    return None


def build_quote(client_id: int, people_count: int, policy: Policy) -> dict[str, Any] | None:
    """Собирает КП по шаблону; None, если шаблона на такое число людей нет."""
    template = find_quote_template(client_id, people_count)
    if template is None:
        return None
    catalog = _catalog()
    lines, total = [], 0.0
    for entry in template["items"]:
        item = catalog[entry["sku"]]
        line_total = item["price"] * entry["qty"]
        total += line_total
        lines.append(
            {"sku": item["sku"], "name": item["name"], "qty": entry["qty"],
             "unit_price": item["price"], "line_total": line_total}
        )
    client = get_client(client_id)
    status = client["status"] if client else "new"
    return {
        "template_id": template["template_id"],
        "name": template["name"],
        "people_count": people_count,
        "lines": lines,
        "total": total,
        "bundle_max_pct": policy_calc.calc_allowed_discount(
            policy, people_count, total, status, is_bundle=bool(template.get("bundle"))
        ),
    }


# ---------------------------------------------------------------------------
# Конфиденциальность (Ст. 4)
# ---------------------------------------------------------------------------
def foreign_client_markers(client_id: int) -> set[str]:
    """Идентифицирующие данные ДРУГИХ клиентов (для валидатора V4)."""
    markers: set[str] = set()
    for cid, client in _clients().items():
        if cid == client_id:
            continue
        name = client["name"]
        markers.add(name.lower())
        # Название внутри кавычек («ГрандСнаб») — устойчивый маркер юрлица;
        # фамилия из ФИО — маркер частного лица. Обрубки вроде «ООО» не нужны.
        quoted = re.findall(r"[«\"']([^»\"']{3,})[»\"']", name)
        markers.update(q.lower() for q in quoted)
        if not quoted:
            markers.update(w.lower() for w in name.split() if len(w) >= 4)
        # Контактное лицо — персональные данные клиента (Ст. 4): и ФИО целиком, и фамилия.
        contact = (client.get("contact") or "").strip()
        if contact:
            markers.add(contact.lower())
            surname = contact.split()[-1]
            if len(surname) >= 5:  # имена не берём: «Анна» — не идентификатор, а менеджер в шапке тоже «Анна»
                markers.add(surname.lower())
        for key in ("company", "email"):
            if client.get(key):
                markers.add(client[key].lower())
        if client.get("phone"):
            markers.add(re.sub(r"\D", "", client["phone"])[-10:])
        for order in client["orders"]:
            markers.add(order["order_id"].lower())
            for line in order["items"]:
                markers.update(s.lower() for s in line.get("serials", []))
    return {m for m in markers if len(m) >= 4}
