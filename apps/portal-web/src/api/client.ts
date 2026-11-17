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
        action: "BUY_CE_BIAS",
        hybrid_score: 0.62,
        tech_score: 0.71,
        news_score: 0.38,
        confidence: 0.74,
        expiry_hint: "weekly",
        updated_at: new Date().toISOString(),
        reason_kind: "technical",
        reason_summary: "EMA+RSI+ST",
        reason_detail:
          "Technical:\n- EMA9 > EMA21 on 15m\n- RSI(14) 58 - rising\n- Supertrend bullish",
        reasons: ["EMA9 > EMA21 on 15m", "RSI(14) 58 - rising", "Supertrend bullish"],
      },
      {
        id: "local-bn",
        symbol: "BANKNIFTY",
        price: 52000,
        ltp: null,
        price_source: "last_known",
        timeframe: "15m",
        action: "HOLD",
        hybrid_score: 0.12,
        tech_score: 0.2,
        news_score: -0.05,
        confidence: 0.4,
        expiry_hint: "weekly",
        updated_at: new Date().toISOString(),
        reason_kind: "neutral",
        reason_summary: "neutral",
        reason_detail: "Hybrid in neutral band",
        reasons: ["Hybrid in neutral band"],
      },
      {
        id: "local-rel",
        symbol: "RELIANCE",
        price: 2920,
        ltp: null,
        price_source: "last_known",
        timeframe: "15m",
        action: "BUY_PE_BIAS",
        hybrid_score: -0.55,
        tech_score: -0.48,
        news_score: -0.67,
        confidence: 0.68,
        expiry_hint: "monthly",
        updated_at: new Date().toISOString(),
        reason_kind: "hybrid",
        reason_summary: "EMA+VWAP+news",
        reason_detail:
          "Technical:\n- EMA9 < EMA21\n- Close below VWAP\n\nNews:\n- Bearish news cluster",
        reasons: ["EMA9 < EMA21", "Close below VWAP", "Bearish news cluster"],
      },
      {
        id: "local-tcs",
        symbol: "TCS",
        price: 3900,
        ltp: null,
        price_source: "last_known",
        timeframe: "15m",
        action: "HOLD",
        hybrid_score: -0.08,
        tech_score: 0.1,
        news_score: -0.35,
        confidence: 0.36,
        expiry_hint: "monthly",
        updated_at: new Date().toISOString(),
        reason_kind: "veto",
        reason_summary: "news veto",
        reason_detail: "Veto:\n- News/TA conflict - hold",
        reasons: ["News/TA conflict - hold"],
      },
    ],
    total: 4,
    offset: 0,
    limit: 50,
    source: "local-mock",
  },
};
