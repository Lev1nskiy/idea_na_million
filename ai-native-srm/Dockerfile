# AI-native CRM — образ для API, UI и Telegram-бота (Шаг 6).
# Один образ на три сервиса: различаются только командой запуска (см. docker-compose.yml).
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=UTC

WORKDIR /app

# Сначала зависимости — слой кэшируется и не пересобирается при правках кода.
# Зависимости фиксируем версиями из requirements.txt (без aiogram-мусора на этапе API).
COPY requirements.txt requirements-llm.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt
# Облачные LLM (GigaChat) — по желанию: docker build --build-arg WITH_LLM=1 .
ARG WITH_LLM=0
RUN if [ "$WITH_LLM" = "1" ]; then pip install -r requirements-llm.txt; fi

COPY . .

# Каталог для SQLite: в compose сюда монтируется volume, чтобы база жила между рестартами.
ENV DB_URL=sqlite:////data/crm.db
RUN mkdir -p /data && useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app /data
USER appuser

EXPOSE 8000

# По умолчанию — API; для UI и бота команда переопределяется в compose.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# Проверка живости: используется в compose healthcheck (curl в slim нет — берём stdlib).
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).status == 200 else 1)"
