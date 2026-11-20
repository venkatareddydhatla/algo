const API_BASE = import.meta.env.VITE_API_BASE?.replace(/\/$/, "") || "";

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const err = (await res.json()) as { detail?: string };
      if (err.detail) detail = String(err.detail);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export type SignalRow = {
  id: string;
  symbol: string;
  /** Display mark: live LTP when available, else candle close / last-known. */
  price?: number | null;
  /** Live LTP only (null when falling back to candle / last-known). */
  ltp?: number | null;
  price_source?: "ltp" | "candle_close" | "last_known" | string | null;
  timeframe: string;
  action: string;
  hybrid_score: number;
  tech_score: number;
  news_score: number;
  confidence: number;
  expiry_hint?: string;
  updated_at: string;
  reasons?: string[];
  reason_kind?: string;
  reason_summary?: string;
  reason_detail?: string;
  stub?: boolean;
};

export type NewsRow = {
  id: string;
  published_at: string;
  score: number;
  label: string;
  symbols: string[];
  title: string;
  source: string;
};

export type WatchlistRow = {
  symbol: string;
  name: string;
  kind: string;
  expiry_rule: string;
  lot_size?: number | null;
  active_scan: boolean;
};

export type Paged<T> = {
  items: T[];
  total?: number;
  offset?: number;
  limit?: number;
  source: string;
  note?: string;
  universe?: string;
  max_active_symbols?: number;
  active_scan_count?: number;
};

export type TopHybrid = {
  ce: SignalRow[];
  pe: SignalRow[];
  limit: number;
  source: string;
  note?: string;
};

export type IndexQuote = {
  symbol: string;
  last_price?: number | null;
  change?: number | null;
  change_pct?: number | null;
  source?: string | null;
  stale?: boolean;
  as_of?: string | null;
  instrument_key?: string | null;
};

export type MarketIndicesPayload = {
  market_status: "OPEN" | "CLOSE" | string;
  trading_day?: boolean;
  session?: { open_ist: string; close_ist: string; tz: string };
  as_of_ist?: string;
  indices: IndexQuote[];
  poll_hint_sec?: number;
};

export type BacktestFill = {
  entry_ts: string;
  exit_ts: string;
  /** Display timestamp, e.g. ``2024-01-15 09:30 IST`` */
  entry_at_ist?: string;
  exit_at_ist?: string;
  side: "CE" | "PE";
  entry_price: number;
  exit_price: number;
  pnl: number;
  bars_held: number;
  entry_action: string;
  exit_reason: string;
};

export type BacktestResult = {
  symbol: string;
  mode: string;
  dry_run: boolean;
  source: string;
  from_date?: string | null;
  to_date?: string | null;
  bars: number;
  bars_evaluated: number;
  trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  pnl: number;
  max_drawdown: number;
  profit_factor: number;
  avg_trade_pnl: number;
  fills: BacktestFill[];
  note?: string;
  /** True when bars are synthetic — headline metrics are not tradeable expectancy. */
  metrics_are_synthetic?: boolean;
  win_rate_label?: string | null;
  pnl_label?: string | null;
};

function qs(params: Record<string, string | number | boolean | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === "") continue;
    sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export const api = {
  signals: (opts?: { offset?: number; limit?: number; symbol?: string }) =>
    getJson<Paged<SignalRow>>(
      `/api/v1/signals${qs({
        offset: opts?.offset ?? 0,
        limit: opts?.limit ?? 50,
        symbol: opts?.symbol,
        expand_watchlist: true,
      })}`,
    ),
  topHybrid: (opts?: { limit?: number }) =>
    getJson<TopHybrid>(
      `/api/v1/signals/top-hybrid${qs({
        limit: opts?.limit ?? 5,
        expand_watchlist: true,
      })}`,
    ),
  watchlist: (opts?: { offset?: number; limit?: number; q?: string }) =>
    getJson<Paged<WatchlistRow>>(
      `/api/v1/watchlist${qs({
        offset: opts?.offset ?? 0,
        limit: opts?.limit ?? 50,
        q: opts?.q,
      })}`,
    ),
  backtestRun: (body: {
    symbol: string;
    bars?: number;
    from_date?: string;
    to_date?: string;
    dry_run?: boolean;
    allow_synthetic?: boolean;
  }) => postJson<BacktestResult>("/api/v1/backtest/run", body),
  news: () => getJson<{ items: NewsRow[]; source: string }>("/api/v1/news"),
  trades: (opts?: { offset?: number; limit?: number; day?: string }) =>
    getJson<Record<string, unknown>>(
      `/api/v1/trades${qs({
        offset: opts?.offset ?? 0,
        limit: opts?.limit ?? 50,
        day: opts?.day,
      })}`,
    ),
  /** Close one open dry_run paper position (long sell / short cover). */
  closeTrade: (
    symbol: string,
    body?: { reason?: string; confirm?: boolean },
  ) =>
    postJson<Record<string, unknown>>(
      `/api/v1/trades/${encodeURIComponent(symbol)}/close`,
      {
        reason: body?.reason ?? "manual_close",
        confirm: body?.confirm ?? true,
      },
    ),
  health: () => getJson<Record<string, unknown>>("/api/v1/health"),
  config: () => getJson<Record<string, unknown>>("/api/v1/admin/config"),
  marketIndices: () => getJson<MarketIndicesPayload>("/api/v1/market/indices"),
};

/** Local fallbacks when API is offline */
export const MOCK = {
  signals: {
    items: [
      {
        id: "local-nifty",
        symbol: "NIFTY",
        price: 24500,
        ltp: null,
        price_source: "last_known",
        timeframe: "15m",
        action: "HOLD",
        hybrid_score: 0,
        tech_score: 0,
        news_score: 0,
        confidence: 0.2,
        expiry_hint: "weekly",
        updated_at: new Date().toISOString(),
        reason_kind: "pine",
        reason_summary: "index blocked",
        reason_detail: "Technical:\n- index_not_equity — Pine stocks-only",
        reasons: ["index_not_equity — Pine stocks-only"],
      },
      {
        id: "local-bn",
        symbol: "BANKNIFTY",
        price: 52000,
        ltp: null,
        price_source: "last_known",
        timeframe: "15m",
        action: "HOLD",
        hybrid_score: 0,
        tech_score: 0,
        news_score: 0,
        confidence: 0.2,
        expiry_hint: "weekly",
        updated_at: new Date().toISOString(),
        reason_kind: "pine",
        reason_summary: "index blocked",
        reason_detail: "Technical:\n- index_not_equity",
        reasons: ["index_not_equity"],
      },
      {
        id: "local-rel",
        symbol: "RELIANCE",
        price: 2920,
        ltp: null,
        price_source: "last_known",
        timeframe: "15m",
        action: "SELL",
        hybrid_score: -1,
        tech_score: -1,
        news_score: 0,
        confidence: 0.85,
        expiry_hint: "monthly",
        updated_at: new Date().toISOString(),
        reason_kind: "pine",
        reason_summary: "EMA crossunder + candle clear below both",
        reason_detail:
          "Technical:\n- prior bar: EMA13 crossunder EMA21\n- candle clear below both EMAs (confirm)\n- sell_signal=True",
        reasons: [
          "prior bar: EMA13 crossunder EMA21",
          "candle clear below both EMAs (confirm)",
          "sell_signal=True",
        ],
      },
      {
        id: "local-tcs",
        symbol: "TCS",
        price: 3900,
        ltp: null,
        price_source: "last_known",
        timeframe: "15m",
        action: "HOLD",
        hybrid_score: 0,
        tech_score: 0,
        news_score: 0,
        confidence: 0.3,
        expiry_hint: "monthly",
        updated_at: new Date().toISOString(),
        reason_kind: "pine",
        reason_summary: "no Pine signal",
        reason_detail: "Technical:\n- buy_signal=False sell_signal=False → HOLD",
        reasons: ["buy_signal=False sell_signal=False → HOLD"],
      },
    ],
    total: 4,
    offset: 0,
    limit: 50,
    source: "local-mock",
  },
};
