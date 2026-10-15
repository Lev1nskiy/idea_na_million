"""Главная страница Streamlit: состояние стенда и навигация по демо (Шаг 5).

Запуск: streamlit run ui/Home.py
"""
from __future__ import annotations

import streamlit as st

from api_client import ApiError, health

st.set_page_config(page_title="AI-native CRM", page_icon="🤖", layout="wide")

st.title("AI-native CRM — демо-стенд")
st.caption("Диалоговый ИИ-ассистент: автономные решения A–H, Smooth & Hold, Policy Management")

try:
    info = health()
    left, mid, right = st.columns(3)
    left.metric("API", "онлайн")
    mid.metric("LLM-провайдер", info.get("provider", "—"))
    right.metric("Версия политики", info.get("policy_version", "—"))
    if "mock" in str(info.get("provider", "")):
        st.info(
            "Провайдер `mock` — детерминированные ответы без сети (страховка на защите). "
            "Для GigaChat задайте GIGACHAT_CREDENTIALS и LLM_PROVIDER=gigachat в .env."
        )
except ApiError as exc:
    st.error(f"API недоступен: {exc}")
    st.code("uvicorn app.main:app --reload --port 8000", language="bash")
    st.stop()

st.subheader("Сценарий демонстрации (5 минут)")
st.markdown(
    """
    1. **1_Client_Chat** — 6 быстрых кнопок: FAQ, Скидка, Гарантия, Спор, Injection, Смена политики.
    2. **3_Policy_Admin** — поменяйте максимальную скидку 15% → 10% и повторите запрос в чате:
       ответ изменится сразу, без перезапуска (Ст. 2).
    3. **2_Manager_Dashboard** — спор уходит в HOLD, видно саммари, кнопки takeover и override.
    4. **4_Security_Metrics** — 7 метрик ТЗ, лог инцидентов и решения с override.
    """
)

st.subheader("Куда смотреть в коде")
st.markdown(
    """
    | Слой | Файл | Что делает |
    |---|---|---|
    | Perception | `core/perception.py` | нормализация + лексический pre-classifier (< 100 мс) |
    | Understanding | `core/understanding.py` | LLM → структурированный JSON (интент, сущности, sentiment) |
    | Decision | `core/decision.py` | категория F > I > G/H > D > E > C > B > A, граница C/I |
    | Action | `core/action.py` | tools → FACTS → ответ → валидаторы Устава |
    | Smooth & Hold | `core/smooth_hold.py` | ACK без LLM, HOLD-цикл, напоминания менеджеру |
    | Handoff | `core/handoff.py` | саммари для менеджера (≤ 30 сек на чтение) |
    | Learning | `core/learning.py` | 7 метрик ТЗ + TTFR раздельно + override_rate |
    | API | `app/routers/*.py` | тонкие роутеры над ядром |
    """
)
