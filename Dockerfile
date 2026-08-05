FROM python:3.12-slim

LABEL org.opencontainers.image.title="x-narrative-tracker" \
      org.opencontainers.image.description="Crypto narrative ingestion, signal tracking, and analytics dashboard"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    DATABASE_PATH=/app/data/x_narrative_tracker.sqlite3 \
    ACCOUNTS_PATH=/app/resources/accounts.json \
    NARRATIVES_PATH=/app/resources/narratives.json \
    SAMPLE_POSTS_PATH=/app/resources/sample_posts.json \
    RSS_FEEDS_PATH=/app/resources/rss_feeds.json \
    CONTENT_SOURCES_PATH=/app/config/sources.json \
    DASHBOARD_HOST=0.0.0.0 \
    DASHBOARD_PORT=8000 \
    TRACKER_ENABLED=true \
    AI_PROVIDER=mock \
    LOG_FORMAT=json

WORKDIR /app

RUN groupadd --gid 10001 tracker \
    && useradd --uid 10001 --gid tracker --create-home --home-dir /home/tracker tracker

COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir --requirement requirements.txt

COPY --chown=10001:10001 app ./app
COPY --chown=10001:10001 data ./resources
COPY --chown=10001:10001 config ./config

RUN mkdir -p /app/data /app/exports \
    && chown 10001:10001 /app/data /app/exports

USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=3).read()"]

CMD ["python", "-m", "app.deployment"]
