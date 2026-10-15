"""Слияние демо-данных двух проектов (шаг «frontend», идемпотентно).

Запуск: `python scripts/merge_frontend_data.py` из корня `ai-native-srm/`.

Что делает:
  1. `data/catalog.json` дополняется товарами сайта-фронтенда (ЛазерФокс/СканФокс/…),
     существующие позиции не трогаются — тесты и сценарии бэкенда продолжают работать.
  2. `data/clients_history.json` пересобирается: идентичности клиентов берутся с сайта
     (ООО «ГрандСнаб», АО «НордБанк» и т. д. — это то, что видит жюри на дашборде),
     а заказы объединяются: мои (с серийниками, на них держатся гарантийные сценарии)
     плюс заказы сайта (их SKU).

Идемпотентно: повторный запуск даёт тот же результат (сначала бэкап, потом запись).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"

# Сегмент сайта (std/corp/new/loyal) — канон; в движке тому же смыслу отвечает
# `status` (он влияет на лояльную скидку: policy.discounts.loyal_statuses).
SEGMENT_TO_STATUS = {"new": "new", "std": "regular", "corp": "corporate", "loyal": "loyal"}

# Канон идентификаторов сайта (frnt: C-1001…C-1006) — порядок их справочника, он же в сценариях
# на странице чата и в текстах демо. Наши client_id свои, поэтому связь хранится в данных.
SITE_IDS = {
    "ООО «Альфа Принт»": "C-1005",
    "ООО «ГрандСнаб»": "C-1001",
    "АО «НордБанк»": "C-1003",
    "ИП Соколова А. В.": "C-1004",
    "ООО «Меридиан Логистика»": "C-1006",
    "ООО «Вектор Трейд»": "C-1002",
}

# --- 1. Каталог: позиции сайта (SKU, категория для поиска, габариты демо-данных) -------------
FRONTEND_ITEMS = [
    {"sku": "MF-401A", "name": "МФУ ЛазерФокс MF-401A", "category": "mfu", "price": 28900,
     "stock": 12, "lead_time_days": 2, "warranty_months": 24,
     "description": "А4, печать/скан/копир, 38 стр/мин, дуплекс, сеть",
     "compat": ["Windows", "Linux", "1С"]},
    {"sku": "LP-220", "name": "Принтер ЛазерФокс LP-220", "category": "printer", "price": 14500,
     "stock": 40, "lead_time_days": 1, "warranty_months": 24,
     "description": "А4 монохром, 30 стр/мин, USB/Ethernet",
     "compat": ["Windows", "macOS", "1С"]},
    {"sku": "MF-510C", "name": "МФУ цветное ЛазерФокс MF-510C", "category": "mfu", "price": 46700,
     "stock": 6, "lead_time_days": 3, "warranty_months": 24,
     "description": "А4 цвет, 26 стр/мин, дуплекс, Wi-Fi",
     "compat": ["Windows", "macOS"]},
    {"sku": "PR-A3P", "name": "Принтер А3 ЛазерФокс PRO A3", "category": "printer", "price": 88000,
     "stock": 3, "lead_time_days": 5, "warranty_months": 24,
     "description": "А3 монохром, 45 стр/мин, 2 лотка",
     "compat": ["Windows", "Linux"]},
    {"sku": "MF-770", "name": "МФУ производственное ЛазерФокс MF-770", "category": "mfu", "price": 210000,
     "stock": 1, "lead_time_days": 10, "warranty_months": 24,
     "description": "А3, 70 стр/мин, финишер-опция, нагрузка 80k/мес",
     "compat": ["Windows", "Linux", "1С"]},
    {"sku": "SC-800", "name": "Сканер СканФокс SC-800", "category": "scanner", "price": 21000,
     "stock": 9, "lead_time_days": 2, "warranty_months": 24,
     "description": "Протяжный, 60 стр/мин, АДФ 80 листов",
     "compat": ["Windows"]},
    {"sku": "LBL-200", "name": "Принтер этикеток ЛейблФокс LBL-200", "category": "printer", "price": 17300,
     "stock": 15, "lead_time_days": 1, "warranty_months": 24,
     "description": "Термопечать, 203 dpi, USB/Bluetooth",
     "compat": ["Windows", "1С"]},
    {"sku": "SHR-150", "name": "Шредер ШредФокс SHR-150", "category": "office", "price": 9800,
     "stock": 21, "lead_time_days": 1, "warranty_months": 12,
     "description": "4-й уровень секретности, 15 листов",
     "compat": []},
    {"sku": "UPS-1000", "name": "ИБП ПауэрФокс UPS-1000", "category": "office", "price": 12400,
     "stock": 14, "lead_time_days": 1, "warranty_months": 24,
     "description": "1000 ВА / 600 Вт, LCD, 4 розетки",
     "compat": []},
    {"sku": "CRG-712", "name": "Картридж ЛазерФокс CRG-712", "category": "consumable", "price": 3900,
     "stock": 120, "lead_time_days": 0,
     "description": "Для LP-220, ресурс 1500 стр.",
     "compat": ["LP-220"]},
    {"sku": "CRG-725", "name": "Картридж ЛазерФокс CRG-725", "category": "consumable", "price": 4200,
     "stock": 80, "lead_time_days": 0,
     "description": "Для MF-401A / MF-510C, ресурс 2100 стр.",
     "compat": ["MF-401A", "MF-510C"]},
    {"sku": "FIN-500", "name": "Финишер ЛазерФокс FIN-500", "category": "office", "price": 54000,
     "stock": 2, "lead_time_days": 7, "warranty_months": 24,
     "description": "Сортировка, степлирование, для MF-770",
     "compat": ["MF-770"]},
    {"sku": "PAP-A4", "name": "Бумага А4 офисная (коробка 5 пачек)", "category": "consumable", "price": 1850,
     "stock": 300, "lead_time_days": 0,
     "description": "80 г/м², класс C",
     "compat": []},
    {"sku": "SRV-PRO", "name": "Сервисный контракт «Про» на 12 мес.", "category": "service", "price": 36000,
     "stock": None, "lead_time_days": 0,
     "description": "Выезд инженера, ТО 4 раза/год, расходники −10%",
     "compat": []},
]


def order(order_id: str, date: str, status: str, total: int, items: list[dict], delay_days: int = 0) -> dict:
    return {"order_id": order_id, "date": date, "status": status, "delay_days": delay_days,
            "total": total, "items": items}


# --- 2. Клиенты: идентичности с сайта + заказы обоих проектов --------------------------------
CLIENTS = [
    {
        "client_id": 1, "name": "ООО «Альфа Принт»", "contact": "Дмитрий Глаголев",
        "segment": "new",
        "phone": "+7 921 555-19-40", "email": "d.glagolev@alfaprint.ru", "company": "ООО «Альфа Принт»",
        "telegram_id": None,
        "internal_notes": "новый лид, истории заказов нет — кейс «гарантия без заказа»: собираем данные, не отказываем",
        "orders": [],
    },
    {
        "client_id": 2, "name": "ООО «ГрандСнаб»", "contact": "Ирина Лебедева",
        "segment": "std",
        "phone": "+7 921 334-88-12", "email": "lebedeva@grandsnab.ru", "company": "ООО «ГрандСнаб»",
        "telegram_id": None,
        "internal_notes": "закупает расходники ежеквартально",
        "orders": [
            order("ORD-1001", "2026-09-20", "delivered", 74700, [
                {"sku": "PX-L3300D", "qty": 3, "price": 24900,
                 "serials": ["SN-PXL33-A100211", "SN-PXL33-A100212", "SN-PXL33-A100213"]}]),
            order("ЗК-1101", "2025-11-03", "delivered", 28900, [
                {"sku": "MF-401A", "qty": 1, "price": 28900, "serials": ["SN-88412"]}]),
            order("ЗК-1188", "2026-01-20", "delivered", 16800, [
                {"sku": "CRG-725", "qty": 4, "price": 4200, "serials": []}]),
        ],
    },
    {
        "client_id": 3, "name": "АО «НордБанк»", "contact": "Марина Северова",
        "segment": "corp",
        "phone": "+7 495 777-03-21", "email": "m.severova@nordbank.ru", "company": "АО «НордБанк»",
        "telegram_id": None,
        "internal_notes": "крупный корпоративный клиент, чувствительные бюджеты (Ст. 4)",
        "orders": [
            order("ORD-2001", "2026-08-14", "delivered", 149800, [
                {"sku": "PX-M4800C", "qty": 2, "price": 74900, "serials": ["SN-PXM48-C200101", "SN-PXM48-C200102"]}]),
            order("ЗК-1330", "2026-01-15", "delivered", 264000, [
                {"sku": "MF-770", "qty": 1, "price": 210000, "serials": []},
                {"sku": "FIN-500", "qty": 1, "price": 54000, "serials": []}]),
            order("ЗК-1402", "2026-01-15", "delivered", 36000, [
                {"sku": "SRV-PRO", "qty": 1, "price": 36000, "serials": []}]),
        ],
    },
    {
        "client_id": 4, "name": "ИП Соколова А. В.", "contact": "Анна Соколова",
        "segment": "std",
        "phone": "+7 911 208-71-55", "email": "sokolova.ip@mail.ru", "company": None,
        "telegram_id": None,
        "internal_notes": "доставка заказа просрочена на 3 дня — кейс компенсации (Ст. 5, H)",
        "orders": [
            order("ORD-3001", "2026-07-02", "delivered", 41900, [
                {"sku": "PX-C5200", "qty": 1, "price": 41900, "serials": []}]),
            order("ЗК-2044", "2026-06-22", "overdue", 29000, [
                {"sku": "LP-220", "qty": 2, "price": 14500, "serials": []}], delay_days=3),
        ],
    },
    {
        "client_id": 5, "name": "ООО «Меридиан Логистика»", "contact": "Ольга Ратнер",
        "segment": "loyal",
        "phone": "+7 812 318-94-07", "email": "oratner@meridianlog.ru", "company": "ООО «Меридиан Логистика»",
        "telegram_id": None,
        "internal_notes": "постоянный клиент, бюджет на оснащение до 450 000 ₽",
        "orders": [
            order("ORD-4001", "2026-05-30", "delivered", 259800, [
                {"sku": "PX-M6500", "qty": 2, "price": 129900, "serials": ["SN-PXM65-V400101", "SN-PXM65-V400102"]}]),
            order("ЗК-1518", "2025-09-30", "delivered", 137450, [
                {"sku": "MF-401A", "qty": 5, "price": 28900, "serials": ["SN-90113", "SN-90114"]}]),
        ],
    },
    {
        "client_id": 6, "name": "ООО «Вектор Трейд»", "contact": "Павел Сорокин",
        "segment": "std",
        "phone": "+7 812 600-45-90", "email": "p.sorokin@vectortrade.ru", "company": "ООО «Вектор Трейд»",
        "telegram_id": None,
        "internal_notes": "закупает бумагу и расходники; для гарантийного кейса «заказ не найден» подходит любой неизвестный серийник",
        "orders": [
            order("ORD-5001", "2026-09-05", "delivered", 38900, [
                {"sku": "PX-M4200", "qty": 1, "price": 38900, "serials": ["SN-PXM42-N500101"]}]),
            order("ЗК-1206", "2026-02-11", "delivered", 18500, [
                {"sku": "PAP-A4", "qty": 10, "price": 1850, "serials": []}]),
        ],
    },
]


def _with_status(client: dict) -> dict:
    """Один источник истины: сегмент сайта задаёт движковый status, имя — идентификатор сайта."""
    ordered = {"client_id": client["client_id"], "site_id": SITE_IDS[client["name"]]}
    for key, value in client.items():
        if key == "client_id":
            continue
        ordered[key] = value
        if key == "segment":
            ordered["status"] = SEGMENT_TO_STATUS[value]
    return ordered


def main() -> None:
    catalog_path, clients_path = DATA / "catalog.json", DATA / "clients_history.json"
    for path in (catalog_path, clients_path):
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))

    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    known = {item["sku"] for item in catalog["items"]}
    added = [item for item in FRONTEND_ITEMS if item["sku"] not in known]
    catalog["items"].extend(added)
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    clients = [_with_status(client) for client in CLIENTS]
    clients_path.write_text(
        json.dumps({"clients": clients}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"каталог: +{len(added)} позиций сайта (всего {len(catalog['items'])})")
    print(f"клиенты: {len(CLIENTS)} (идентичности сайта + заказы обоих проектов)")
    for client in clients:
        print(f"  #{client['client_id']} {client['name']:<28} segment={client['segment']:<6} "
              f"status={client['status']:<10} заказов={len(client['orders'])}")


if __name__ == "__main__":
    main()
