FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    RESEARCH_HUB_HOST=0.0.0.0 \
    RESEARCH_HUB_PORT=8080 \
    RESEARCH_HUB_DB=/data/research_hub.sqlite3 \
    RESEARCH_HUB_RUNTIME_CONFIG=/data/runtime_config.json \
    RESEARCH_HUB_STATIC_DIR=/app/web \
    RESEARCH_HUB_EXPORT_DIR=/app/exports

WORKDIR /app

RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /data /app/exports \
    && chown -R appuser:appuser /data /app/exports

COPY --chown=appuser:appuser requirements.txt ./

RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY --chown=appuser:appuser config/__init__.py config/settings.py ./config/
COPY --chown=appuser:appuser research_hub ./research_hub
COPY --chown=appuser:appuser scripts ./scripts
COPY --chown=appuser:appuser web ./web
COPY --chown=appuser:appuser run.py README.md ./

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import json, urllib.request; data=json.load(urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)); raise SystemExit(0 if data.get('status') == 'ok' else 1)"

CMD ["python", "run.py"]
