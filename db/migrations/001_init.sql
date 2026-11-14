-- Options Signal Portal — core schema (Postgres 16+)
-- Applied via docker-entrypoint-initdb.d or migration runner.
-- Timestamps: timestamptz UTC; UI converts to Asia/Kolkata.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "citext";

-- Roles & users
CREATE TYPE user_role_name AS ENUM ('admin', 'analyst', 'guest');

CREATE TABLE users (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email         CITEXT UNIQUE NOT NULL,
  display_name  TEXT NOT NULL,
  password_hash TEXT,
  is_active     BOOLEAN NOT NULL DEFAULT TRUE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE roles (
  id          SMALLSERIAL PRIMARY KEY,
  name        user_role_name UNIQUE NOT NULL,
  description TEXT
);

CREATE TABLE user_roles (
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role_id SMALLINT NOT NULL REFERENCES roles(id),
  PRIMARY KEY (user_id, role_id)
);

-- Instruments & candles
CREATE TYPE exchange_code AS ENUM ('NSE', 'BSE', 'NFO', 'BFO');
CREATE TYPE instrument_kind AS ENUM ('index', 'equity', 'index_fut', 'stock_fut', 'ce', 'pe');

CREATE TABLE instruments (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  exchange        exchange_code NOT NULL,
  symbol          TEXT NOT NULL,
  name            TEXT NOT NULL,
  kind            instrument_kind NOT NULL,
  lot_size        INTEGER,
  tick_size       NUMERIC(12,4),
  is_fno          BOOLEAN NOT NULL DEFAULT FALSE,
  underlying_id   UUID REFERENCES instruments(id),
  strike          NUMERIC(14,4),
  option_type     CHAR(2) CHECK (option_type IN ('CE','PE') OR option_type IS NULL),
  expiry_date     DATE,
  upstox_key      TEXT UNIQUE,
  metadata        JSONB NOT NULL DEFAULT '{}',
  UNIQUE (exchange, symbol, kind, expiry_date, strike, option_type)
);

CREATE TABLE candles (
  instrument_id UUID NOT NULL REFERENCES instruments(id),
  timeframe     TEXT NOT NULL,
  ts            TIMESTAMPTZ NOT NULL,
  open          NUMERIC(14,4) NOT NULL,
  high          NUMERIC(14,4) NOT NULL,
  low           NUMERIC(14,4) NOT NULL,
  close         NUMERIC(14,4) NOT NULL,
  volume        BIGINT NOT NULL DEFAULT 0,
  oi            BIGINT,
  source        TEXT NOT NULL DEFAULT 'upstox',
  PRIMARY KEY (instrument_id, timeframe, ts)
);

CREATE INDEX candles_ts_idx ON candles (timeframe, ts DESC);

-- News & sentiment
CREATE TABLE news_items (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source       TEXT NOT NULL,
  external_id  TEXT,
  url          TEXT,
  title        TEXT NOT NULL,
  body         TEXT,
  published_at TIMESTAMPTZ NOT NULL,
  ingested_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  content_hash CHAR(64) NOT NULL,
  raw          JSONB NOT NULL DEFAULT '{}',
  UNIQUE (source, content_hash)
);

CREATE TABLE news_symbol_links (
  news_id       UUID NOT NULL REFERENCES news_items(id) ON DELETE CASCADE,
  instrument_id UUID NOT NULL REFERENCES instruments(id),
  confidence    NUMERIC(5,4) NOT NULL DEFAULT 1.0,
  PRIMARY KEY (news_id, instrument_id)
);

CREATE TABLE news_sentiment (
  news_id       UUID PRIMARY KEY REFERENCES news_items(id) ON DELETE CASCADE,
  model         TEXT NOT NULL,
  score         NUMERIC(8,6) NOT NULL,
  label         TEXT NOT NULL,
  confidence    NUMERIC(5,4) NOT NULL,
  scored_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  details       JSONB NOT NULL DEFAULT '{}'
);

-- Signals, recommendations, execution
CREATE TYPE signal_action AS ENUM (
  'HOLD', 'BUY_CE_BIAS', 'BUY_PE_BIAS', 'EXIT', 'REDUCE'
);
CREATE TYPE trading_mode AS ENUM ('dry_run', 'demo', 'live');
CREATE TYPE order_status AS ENUM (
  'proposed', 'submitted', 'open', 'partial', 'filled',
  'cancelled', 'rejected', 'simulated'
);

CREATE TABLE signals (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  instrument_id   UUID NOT NULL REFERENCES instruments(id),
  timeframe       TEXT NOT NULL DEFAULT '15m',
  bar_ts          TIMESTAMPTZ NOT NULL,
  action          signal_action NOT NULL,
  tech_score      NUMERIC(8,6),
  news_score      NUMERIC(8,6),
  hybrid_score    NUMERIC(8,6) NOT NULL,
  confidence      NUMERIC(5,4) NOT NULL,
  reasons         JSONB NOT NULL DEFAULT '[]',
  features        JSONB NOT NULL DEFAULT '{}',
  model_versions  JSONB NOT NULL DEFAULT '{}',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (instrument_id, timeframe, bar_ts, action)
);

CREATE TABLE signal_news_links (
  signal_id UUID NOT NULL REFERENCES signals(id) ON DELETE CASCADE,
  news_id   UUID NOT NULL REFERENCES news_items(id),
  weight    NUMERIC(5,4) NOT NULL DEFAULT 1.0,
  PRIMARY KEY (signal_id, news_id)
);

CREATE TABLE option_recommendations (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  signal_id       UUID NOT NULL UNIQUE REFERENCES signals(id) ON DELETE CASCADE,
  legs            JSONB NOT NULL,
  expiry_date     DATE NOT NULL,
  spot_ref        NUMERIC(14,4),
  chain_snapshot  JSONB,
  rationale       TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE orders (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  recommendation_id    UUID REFERENCES option_recommendations(id),
  signal_id            UUID REFERENCES signals(id),
  user_id              UUID REFERENCES users(id),
  mode                 trading_mode NOT NULL,
  status               order_status NOT NULL,
  broker               TEXT NOT NULL DEFAULT 'upstox',
  client_order_id      TEXT NOT NULL UNIQUE,
  broker_order_id      TEXT,
  side                 TEXT NOT NULL,
  product              TEXT NOT NULL,
  quantity             INTEGER NOT NULL,
  limit_price          NUMERIC(14,4),
  instrument_id        UUID REFERENCES instruments(id),
  payload              JSONB NOT NULL DEFAULT '{}',
  error_message        TEXT,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE fills (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id     UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  qty          INTEGER NOT NULL,
  price        NUMERIC(14,4) NOT NULL,
  fees_est     NUMERIC(14,4) DEFAULT 0,
  filled_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  raw          JSONB NOT NULL DEFAULT '{}'
);

CREATE TABLE positions (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  mode            trading_mode NOT NULL,
  instrument_id   UUID NOT NULL REFERENCES instruments(id),
  qty             INTEGER NOT NULL,
  avg_price       NUMERIC(14,4) NOT NULL,
  realized_pnl    NUMERIC(14,4) NOT NULL DEFAULT 0,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (mode, instrument_id)
);

CREATE TABLE pnl_snapshots (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  mode       trading_mode NOT NULL,
  as_of      TIMESTAMPTZ NOT NULL,
  realized   NUMERIC(14,4) NOT NULL,
  unrealized NUMERIC(14,4) NOT NULL,
  details    JSONB NOT NULL DEFAULT '{}',
  UNIQUE (mode, as_of)
);

-- Config, audit, health
CREATE TABLE app_config_versions (
  id          BIGSERIAL PRIMARY KEY,
  key         TEXT NOT NULL,
  value       JSONB NOT NULL,
  version     INTEGER NOT NULL,
  changed_by  UUID REFERENCES users(id),
  changed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (key, version)
);

CREATE TABLE audit_events (
  id          BIGSERIAL PRIMARY KEY,
  ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
  actor_user  UUID REFERENCES users(id),
  category    TEXT NOT NULL,
  action      TEXT NOT NULL,
  entity_type TEXT,
  entity_id   TEXT,
  severity    TEXT NOT NULL DEFAULT 'info',
  payload     JSONB NOT NULL DEFAULT '{}',
  correlation_id UUID
);

CREATE INDEX audit_ts_idx ON audit_events (ts DESC);
CREATE INDEX audit_corr_idx ON audit_events (correlation_id);

CREATE TABLE health_checks (
  id           BIGSERIAL PRIMARY KEY,
  component    TEXT NOT NULL,
  status       TEXT NOT NULL,
  checked_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  latency_ms   INTEGER,
  detail       JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX health_component_ts ON health_checks (component, checked_at DESC);

CREATE TABLE backtest_runs (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name         TEXT NOT NULL,
  params       JSONB NOT NULL,
  metrics      JSONB NOT NULL DEFAULT '{}',
  started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at  TIMESTAMPTZ,
  status       TEXT NOT NULL DEFAULT 'running'
);
