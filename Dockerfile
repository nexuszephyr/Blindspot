# Blindspot service image. Secrets are NEVER baked in: QLOO_API_KEY comes from the host's env.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000 \
    QLOO_CACHE_PATH=/app/data/qloo_cache.sqlite \
    QLOO_SNAPSHOT_PATH=/app/data/snapshot/snapshot.json

WORKDIR /app
COPY requirements.lock ./
RUN pip install -r requirements.lock

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-deps . \
    && useradd --create-home --uid 10001 app \
    && mkdir -p /app/data \
    && chown -R app:app /app/data

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/healthz',timeout=4)"

# Shell form so $PORT (set by hosts like Render) is expanded. X-Forwarded-For handling is done
# by the app itself (TRUSTED_PROXY_HOPS), so uvicorn's own proxy-header rewriting is off.
CMD ["sh", "-c", "exec uvicorn blindspot.service.app:main_app --factory --host 0.0.0.0 --port ${PORT} --no-proxy-headers"]
