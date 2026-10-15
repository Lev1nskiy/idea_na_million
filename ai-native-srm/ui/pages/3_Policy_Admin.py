"""Админка Policy Matrix: правка без перезапуска + история версий (Шаг 5, Ст. 2).

Ключевой демо-момент: меняем максимальную скидку, сохраняем — и следующий ответ ИИ
в чате уже использует новое значение (ядро читает политику из БД на каждое сообщение).
"""
from __future__ import annotations

import json

import streamlit as st

from api_client import ApiError, export_dump, import_dump, policy, policy_history, update_policy

st.set_page_config(page_title="Policy Admin", page_icon="⚙️", layout="wide")
st.title("⚙️ Policy Matrix — админка")
st.caption("Изменения вступают в силу немедленно: перезапуск не нужен (Ст. 2 Устава).")

try:
    current = policy()
except ApiError as exc:
    st.error(str(exc))
    st.stop()

st.metric("Актуальная версия", current["version"])
discounts = current["discounts"]
warranty = current["warranty"]
sla = current["manager_sla"]
thresholds = current["thresholds"]
compensation = (current["delivery_compensation"] or [{}])[0]
tiers = discounts.get("tiers") or []
tier = tiers[0] if tiers else {"min_sum": 0, "pct": 0}

author = st.sidebar.text_input("Автор правки", value="admin")
note = st.sidebar.text_input("Комментарий", value="правка из админки")

with st.form("policy_form"):
    st.subheader("Скидки")
    col1, col2, col3 = st.columns(3)
    max_pct = col1.number_input("Максимальная скидка, %", 0.0, 100.0, float(discounts["max_pct"]), 0.5)
    bundle_pct = col2.number_input("Скидка за комплект, %", 0.0, 100.0, float(discounts["bundle_max_pct"]), 0.5)
    loyal_pct = col3.number_input("Бонус постоянным клиентам, %", 0.0, 50.0, float(discounts["loyal_bonus_pct"]), 0.5)

    st.markdown("**Порог по сумме заказа** (заказ свыше — скидка порога)")
    tcol1, tcol2 = st.columns(2)
    tier_sum = tcol1.number_input("Сумма заказа от, ₽", 0.0, 10_000_000.0, float(tier["min_sum"]), 1000.0)
    tier_pct = tcol2.number_input("Скидка порога, %", 0.0, 100.0, float(tier["pct"]), 0.5)

    st.subheader("Компенсация за задержку доставки")
    ccol1, ccol2 = st.columns(2)
    delay_days = ccol1.number_input("Задержка более, дней", 0, 60, int(compensation.get("delay_days_gt", 2)))
    delay_pct = ccol2.number_input("Компенсация, % от суммы", 0.0, 100.0, float(compensation.get("pct_of_sum", 5.0)), 0.5)

    st.subheader("Гарантия и сроки связи с менеджером")
    gcol1, gcol2, gcol3 = st.columns(3)
    replace_hours = gcol1.number_input("Замена по гарантии, часов", 1, 240, int(warranty["replace_hours"]))
    response_hours = gcol2.number_input("Ответ менеджера в рабочее время, ч", 0, 72, int(sla["response_hours"]))
    off_hours = gcol3.number_input("Ответ менеджера вне часов, ч", 0, 72, int(sla["off_hours_response_hours"]))

    st.subheader("Пороги и тайминги")
    hcol1, hcol2, hcol3, hcol4 = st.columns(4)
    min_conf = hcol1.slider("min_confidence", 0.0, 1.0, float(thresholds["min_confidence"]), 0.05)
    push_limit = hcol2.number_input("Лимит настаиваний на скидке", 1, 10, int(thresholds["discount_push_limit"]))
    reminder = hcol3.number_input("Напоминание менеджеру, мин", 1, 240, int(thresholds["reminder_minutes"]))
    ping = hcol4.number_input("Пинг клиенту в HOLD, мин", 1, 240, int(thresholds["hold_ping_minutes"]))

    submitted = st.form_submit_button("💾 Сохранить новую версию", use_container_width=True)

if submitted:
    patch = {
        "discounts": {
            "max_pct": max_pct,
            "bundle_max_pct": bundle_pct,
            "loyal_bonus_pct": loyal_pct,
            "tiers": [{"min_sum": tier_sum, "pct": tier_pct}],
        },
        "warranty": {"replace_hours": int(replace_hours)},
        "delivery_compensation": [{"delay_days_gt": int(delay_days), "pct_of_sum": delay_pct}],
        "manager_sla": {"response_hours": int(response_hours), "off_hours_response_hours": int(off_hours)},
        "thresholds": {
            "min_confidence": float(min_conf),
            "discount_push_limit": int(push_limit),
            "reminder_minutes": int(reminder),
            "hold_ping_minutes": int(ping),
        },
    }
    try:
        result = update_policy(patch, author=author, note=note)
        st.success(f"Сохранено: версия {result['version']}. Откройте «Чат клиента» и проверьте новый лимит скидки.")
    except ApiError as exc:
        st.error(str(exc))

st.divider()
left, right = st.columns([1, 1])
with left:
    st.subheader("История версий")
    try:
        st.dataframe(policy_history(), use_container_width=True, hide_index=True)
    except ApiError as exc:
        st.error(str(exc))

with right:
    st.subheader("Текущая политика (JSON)")
    st.json(json.loads(json.dumps(current)), expanded=False)

st.divider()
st.subheader("Перенос данных")
st.caption("Дампы содержат полный текст переписки и ПДн — обезличенный вариант безопасен для передачи наружу (Ст. 4).")
from api_client import admin_enabled  # noqa: E402  (раздел необязательный)

if not admin_enabled():
    st.info("Включите ADMIN_TOKEN в .env и перезапустите UI, чтобы работать с дампами состояния.")
else:
    dcol1, dcol2 = st.columns(2)
    if dcol1.button("Скачать обезличенный дамп"):
        dcol1.download_button("state_masked.json", export_dump(mask=True), "state_masked.json")
    if dcol2.button("Скачать полный дамп (закрытый контур)"):
        dcol2.download_button("state.json", export_dump(mask=False), "state.json")

    uploaded = st.file_uploader("Загрузить дамп состояния (JSON)", type="json")
    if uploaded is not None:
        dry = st.checkbox("Только проверка (dry-run, БД не меняется)", value=True)
        with_policy = st.checkbox("Применить политику из дампа новой версией", value=False)
        if st.button("Импортировать"):
            try:
                report = import_dump(uploaded.getvalue(), policy=with_policy, dry_run=dry)
                st.json(report)
            except ApiError as exc:
                st.error(str(exc))
