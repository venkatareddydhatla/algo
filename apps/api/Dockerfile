FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
  && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY packages ./packages
COPY apps/api ./apps/api
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
  && pip install --no-cache-dir -e ./apps/api

ENV PYTHONUNBUFFERED=1
ENV BIND_HOST=0.0.0.0
ENV API_PORT=8000
ENV TRADING_MODE=dry_run

WORKDIR /app/apps/api
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
