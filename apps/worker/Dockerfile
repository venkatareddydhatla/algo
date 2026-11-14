FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY packages ./packages
COPY apps/worker ./apps/worker
COPY config ./config

RUN pip install --no-cache-dir -e ./packages/domain \
  -e ./packages/broker-upstox \
  -e ./packages/ta-core \
  -e ./packages/news-rss \
  -e ./packages/sentiment \
  -e ./packages/hybrid-engine \
  -e ./packages/risk \
  -e ./packages/notify \
  -e ./packages/persistence \
  && pip install --no-cache-dir -e ./apps/worker

ENV PYTHONUNBUFFERED=1
ENV TRADING_MODE=dry_run
ENV WORKER_INTERVAL_SEC=60

WORKDIR /app/apps/worker
CMD ["python", "-m", "app.main"]
