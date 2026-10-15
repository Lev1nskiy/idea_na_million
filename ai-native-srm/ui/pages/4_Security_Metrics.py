"""Метрики ТЗ и безопасность: 7 метрик, инциденты injection, override решений (Шаг 5).

Инциденты показываются ТОЛЬКО в маскированном виде (Ст. 4): сырой текст остаётся в
закрытой таблице messages и доступен менеджеру только в карточке диалога.
"""
from __future__ import annotations

import streamlit as st

from api_client import ApiError, decisions, incidents, metrics, override_decision

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:  # pragma: no cover
    st_autorefresh = None

st.set_page_config(page_title="Метрики и безопасность", page_icon="📊", layout="wide")
st.title("📊 Метрики ТЗ и безопасность")

if st_autorefresh is not None:
    st_autorefresh(interval=5000, key="metrics_poll")

try:
    data = metrics()
    incidents_rows = incidents(limit=50)
    decisions_rows = decisions(limit=50)
except ApiError as exc:
    st.error(str(exc))
    st.stop()

st.caption(f"Расчёт на {data['generated_at'][:19]} UTC · диалогов в базе: {data['conversations']}")

st.subheader("Семь метрик ТЗ")
row1 = st.columns(4)
ttfr = data["ttfr"]
row1[0].metric(
    "TTFR I/F (pre-classifier), p95",
    f"{ttfr['IF_pre_classifier']['p95_ms'] or 0:.0f} мс",
    help="Споры и инъекции, пойманные лексиконом без LLM — цель < 100 мс",
)
row1[1].metric("Доля I/F < 100 мс", f"{(ttfr['IF_pre_classifier']['share_under_threshold'] or 0) * 100:.0f}%")
row1[2].metric("TTFR прочих, p95", f"{ttfr['other']['p95_ms'] or 0:.0f} мс", help="Цель < 5000 мс")
row1[3].metric("INTERIM срабатываний", ttfr["interim_count"])

row2 = st.columns(4)
row2[0].metric("Auto-decision rate", f"{data['auto_decision_rate_pct'] or 0:.1f}%", help="Диалоги A–H / все диалоги")
row2[1].metric(
    "Retention в спорах",
    f"{data['client_retention_in_disputes']['retention_pct'] or 0:.0f}%",
    help=f"Споров: {data['client_retention_in_disputes']['disputes']}, из них в HOLD: "
         f"{data['client_retention_in_disputes']['still_holding']}",
)
row2[2].metric(
    "Price hallucination",
    f"{data['price_hallucination']['intercepted']} перехвачено",
    delta="0 доставлено",
    delta_color="normal",
    help=data["price_hallucination"]["note"],
)
row2[3].metric(
    "Injection resistance",
    f"{data['injection_resistance']['resistance_pct'] or 0:.0f}%",
    help=f"Инъекционных диалогов: {data['injection_resistance']['conversations']}, "
         f"инцидентов: {data['injection_resistance']['incidents']}",
)

row3 = st.columns(4)
coverage = data["off_hours_coverage"]
row3[0].metric("Off-hours coverage", f"{coverage['off_hours_pct'] or 0:.0f}%", help="Вне рабочих часов, ответ ≤ 5 с")
row3[1].metric(
    "Ответ менеджера, p50 / p95",
    f"{data['manager_response_time']['p50_min'] or 0:.0f} / {data['manager_response_time']['p95_min'] or 0:.0f} мин",
    help=f"Ждут сейчас: {data['manager_response_time']['waiting_now']}",
)
row3[2].metric("Override rate", f"{data['override_rate']['overall_pct'] or 0:.1f}%")
recall = data["pre_classifier_recall"]
row3[3].metric(
    "Recall pre-classifier",
    f"{(recall.get('recall') or 0) * 100:.0f}%",
    help=f"Поймано лексиконом на тест-наборе: {recall.get('n_disputes', 0)} споров, "
         f"ложных срабатываний {recall.get('false_positive_rate', 0) * 100:.0f}% "
         f"на {recall.get('n_neutral', 0)} нейтральных фразах",
)

with st.expander("Споры: кто поймал (pre-classifier против LLM) и разрез по категориям"):
    st.json(
        {
            "disputes_caught_by": data["disputes_caught_by"],
            "override_by_category": data["override_rate"]["by_category"],
            "pre_classifier": data["pre_classifier_recall"],
        }
    )

suggestions = data.get("policy_suggestions") or []
if suggestions:
    st.subheader("Подсказки по политике")
    for item in suggestions:
        st.markdown(f"- {item}")

st.divider()
left, right = st.columns([1, 1])

with left:
    st.subheader("🛡️ Инциденты безопасности (маскировано, Ст. 4)")
    if incidents_rows:
        st.dataframe(
            [
                {
                    "id": row["id"],
                    "диалог": row["conversation_id"],
                    "паттерн": row["pattern"],
                    "текст (маска)": row["raw_text_masked"][:80],
                    "hash": row["text_hash"],
                    "время": row["ts"][11:19],
                }
                for row in incidents_rows
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("Инцидентов нет. Проверьте сценарий Injection на странице «Чат клиента».")

with right:
    st.subheader("✏️ Журнал решений и override")
    if decisions_rows:
        st.dataframe(
            [
                {
                    "id": row["id"],
                    "диалог": row["conversation_id"],
                    "кат.": row["category"],
                    "маршрут": row["route"],
                    "conf.": round(row["confidence"], 2),
                    "политика": f"v{row['policy_version']}",
                    "override": row["overridden_by"] or "",
                }
                for row in decisions_rows
            ],
            use_container_width=True,
            hide_index=True,
        )
        target = st.selectbox(
            "Решение для override",
            options=decisions_rows,
            format_func=lambda d: f"#{d['id']} · диалог {d['conversation_id']} · кат. {d['category']}",
        )
        reason = st.text_input("Причина override", placeholder="например: клиент ошибся категорией")
        manager = st.text_input("Менеджер", value="Анна")
        if st.button("Зафиксировать override") and reason.strip():
            try:
                override_decision(target["id"], manager, reason.strip())
                st.success("Записано: метрика override_rate обновится при следующем обновлении.")
                st.rerun()
            except ApiError as exc:
                st.error(str(exc))
    else:
        st.info("Решений пока нет.")
