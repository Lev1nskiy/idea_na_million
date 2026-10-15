"""Дашборд менеджера: очередь, саммари Handoff, ответ клиенту, override, resolve (Шаг 5).

Порядок блоков карточки задан роадмапом: саммари ВВЕРХУ (читается за 30 секунд), затем
переписка и действия. HOLD-диалоги подсвечены, DISPUTE-уведомления видны сразу.
"""
from __future__ import annotations

import streamlit as st

from api_client import (
    ApiError,
    conversation,
    conversations,
    manager_reply,
    mark_all_notifications_read,
    messages as fetch_messages,
    mark_notification_read,
    notifications,
    override_decision,
    resolve,
    takeover,
)

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:  # pragma: no cover
    st_autorefresh = None

st.set_page_config(page_title="Дашборд менеджера", page_icon="🧑‍💻", layout="wide")
st.title("🧑‍💻 Дашборд менеджера")

if st_autorefresh is not None:
    st_autorefresh(interval=4000, key="dashboard_poll")

MANAGER = st.sidebar.text_input("Менеджер", value="Анна")
st.sidebar.caption("Имя попадает в takeover, ответы и override — по нему считается метрика времени реакции.")

# --- Уведомления -----------------------------------------------------------------------------
try:
    notes = notifications(active_only=True)
except ApiError as exc:
    st.error(str(exc))
    st.stop()

if notes:
    head, action = st.columns([6, 1])
    head.subheader(f"🔔 Активные уведомления: {len(notes)}")
    if action.button("Разобрать все"):
        mark_all_notifications_read()
        st.rerun()
    for note in notes:
        badge = {"DISPUTE": "⚖️", "SECURITY": "🛡️", "OVERRIDE_DUE": "⏰"}.get(note["type"], "🔔")
        level = f" · напоминание {note['reminder_level']}" if note["reminder_level"] else ""
        overdue = " · ⚠️ SLA просрочен" if "просроч" in note["text"].lower() else ""
        with st.container(border=True):
            left, right = st.columns([6, 1])
            left.markdown(
                f"{badge} **{note['type']}** · {note['client_name'] or 'Клиент'} · "
                f"ждёт **{note['waited_min']} мин**{level}{overdue}\n\n{note['text']}"
            )
            if right.button("Прочитано", key=f"read_{note['id']}"):
                mark_notification_read(note["id"])
                st.rerun()
else:
    st.success("Активных уведомлений нет — очередь чистая.")

# --- Очередь диалогов ------------------------------------------------------------------------
st.subheader("Очередь диалогов")
columns = st.columns(3)
COLUMN_STATUSES = (("HOLD", "🔴 Ждут менеджера (HOLD)"), ("ACTIVE", "🟢 Ведёт ИИ"), ("HANDOFF", "🔵 Ведёт менеджер"))

rows: list[dict] = []
for col, (status, title) in zip(columns, COLUMN_STATUSES):
    try:
        items = conversations(status=status)
    except ApiError as exc:
        col.error(str(exc))
        continue
    rows.extend(items)
    col.markdown(f"**{title}** — {len(items)}")
    for item in items:
        label = f"#{item['id']} {item['client_name']} · {item['last_text'] or '—'}"
        if col.button(label[:70], key=f"pick_{status}_{item['id']}", use_container_width=True):
            st.session_state["selected_conversation"] = item["id"]
        marks = []
        if item["suspicious"]:
            marks.append("🛡️ suspicious")
        if item["unread_notifications"]:
            marks.append(f"🔔 {item['unread_notifications']}")
        if item["category"]:
            marks.append(f"кат. {item['category']}")
        if marks:
            col.caption(" · ".join(marks))

# --- Карточка выбранного диалога --------------------------------------------------------------
selected = st.session_state.get("selected_conversation")
if not selected and rows:
    selected = rows[0]["id"]
if not selected:
    st.info("Диалогов пока нет: отправьте сообщение на странице «Чат клиента».")
    st.stop()

try:
    detail = conversation(selected)
except ApiError as exc:
    st.error(str(exc))
    st.stop()

conv, client, deal, handoff = detail["conversation"], detail["client"], detail["deal"], detail["handoff"]
st.divider()
head, meta = st.columns([3, 2])
head.subheader(f"Диалог #{conv['id']} · {client['name'] if client else '—'}")
meta.markdown(
    f"статус **{conv['status']}** · канал {conv['channel']} · категория диалога **{conv['category'] or '—'}**"
    + (f" · менеджер {conv['assigned_manager']}" if conv["assigned_manager"] else "")
    + (" · 🛡️ **suspicious**" if conv["suspicious"] else "")
)

summary = handoff["summary"]
if summary:
    urgency_icon = {"high": "🔥", "medium": "⚡", "low": "🌿"}.get(summary["urgency"], "")
    with st.container(border=True):
        st.markdown(f"### {urgency_icon} Саммари для менеджера (статус Handoff: {handoff['status'] or '—'})")
        st.markdown(f"**Суть спора:** {summary['dispute_essence'] or '—'}")
        col_a, col_b = st.columns(2)
        col_a.markdown("**Требования клиента:**\n" + "\n".join(f"- {d}" for d in summary["client_demands"]) or "—")
        col_b.markdown(
            "**Remedy-опции (ориентир из Policy):**\n"
            + ("\n".join(f"- {r}" for r in summary["remedy_options_mentioned"]) or "—")
        )
        st.markdown(f"**Рекомендуемый диапазон:** {summary['recommended_range'] or '—'}")
        with st.expander("История диалога (сжато)"):
            st.markdown("\n".join(f"- {h}" for h in summary["history_digest"]) or "—")

if deal:
    with st.container(border=True):
        st.markdown("**Карточка сделки**")
        cols = st.columns(5)
        cols[0].metric("Товар", (deal["product"] or "—")[:24])
        cols[1].metric("Кол-во", deal["qty"] or "—")
        cols[2].metric("Бюджет", f"{deal['budget']:,.0f} ₽".replace(",", " ") if deal["budget"] else "—")
        cols[3].metric("Скидка", f"{deal['discount_pct']:g}%" if deal["discount_pct"] is not None else "—")
        cols[4].metric("Стадия", deal["stage"])

# --- Переписка -------------------------------------------------------------------------------
with st.container(border=True):
    st.markdown("**Переписка**")
    try:
        for message in fetch_messages(conv["id"]):
            who = {"client": "🧑‍💼 Клиент", "ai": "🤖 ИИ", "manager": f"🧑‍💻 {MANAGER}"}[message["role"]]
            st.markdown(f"**{who}:** {message['text']}")
            st.caption(f"{message['phase'] or 'FINAL'} · {message['latency_ms'] or '—'} мс")
    except ApiError as exc:
        st.error(str(exc))

with st.expander(f"Решения ИИ и уведомления ({len(detail['decisions'])})"):
    for decision in detail["decisions"]:
        mark = "✏️ override" if decision["overridden_by"] else ""
        st.markdown(
            f"`#{decision['id']}` **{decision['category']}** / {decision['route']} · "
            f"confidence {decision['confidence']:.2f} · политика v{decision['policy_version']} {mark}\n\n"
            f"{'; '.join(decision['reasons'])}\n\n{decision['action_summary'] or ''}"
        )

# --- Действия ---------------------------------------------------------------------------------
st.subheader("Действия")
action_left, action_mid, action_right, action_far = st.columns(4)

with action_left:
    if st.button("🤝 Взять диалог (takeover)", use_container_width=True, disabled=conv["status"] == "RESOLVED"):
        try:
            takeover(conv["id"], MANAGER)
            st.success("Диалог передан менеджеру, уведомления погашены.")
            st.rerun()
        except ApiError as exc:
            st.error(str(exc))

with action_far:
    if st.button("✅ Закрыть диалог", use_container_width=True, disabled=conv["status"] == "RESOLVED"):
        try:
            resolve(conv["id"])
            st.success("Диалог закрыт.")
            st.rerun()
        except ApiError as exc:
            st.error(str(exc))

with action_mid:
    reply_text = st.text_area("Ответ клиенту", key=f"reply_{conv['id']}", height=90)
    if st.button("📨 Отправить", use_container_width=True) and reply_text.strip():
        try:
            manager_reply(conv["id"], MANAGER, reply_text.strip())
            st.success("Ответ доставлен в outbox канала.")
            st.rerun()
        except ApiError as exc:
            st.error(str(exc))

with action_right:
    decisions = detail["decisions"]
    if decisions:
        choice = st.selectbox(
            "Override решения", options=decisions, format_func=lambda d: f"#{d['id']} {d['category']} ({d['route']})",
            key=f"override_pick_{conv['id']}",
        )
        reason = st.text_input("Причина", key=f"override_reason_{conv['id']}", placeholder="например: не тот товар")
        if st.button("✏️ Зафиксировать override", use_container_width=True) and reason.strip():
            try:
                override_decision(choice["id"], MANAGER, reason.strip())
                st.success("Override записан — попадёт в override_rate.")
                st.rerun()
            except ApiError as exc:
                st.error(str(exc))
