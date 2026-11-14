const API_BASE = import.meta.env.VITE_API_BASE?.replace(/\/$/, "") || "";

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export type SignalRow = {
  id: string;
  symbol: string;
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
  watchlist: (opts?: { offset?: number; limit?: number; q?: string }) =>
    getJson<Paged<WatchlistRow>>(
      `/api/v1/watchlist${qs({
        offset: opts?.offset ?? 0,
        limit: opts?.limit ?? 50,
        q: opts?.q,
      })}`,
    ),
  news: () => getJson<{ items: NewsRow[]; source: string }>("/api/v1/news"),
  trades: () => getJson<Record<string, unknown>>("/api/v1/trades"),
  health: () => getJson<Record<string, unknown>>("/api/v1/health"),
  config: () => getJson<Record<string, unknown>>("/api/v1/admin/config"),
};

/** Local fallbacks when API is offline */
export const MOCK = {
  signals: {
    items: [
      {
        id: "local-nifty",
        symbol: "NIFTY",
        timeframe: "15m",
        action: "BUY_CE_BIAS",
        hybrid_score: 0.62,
        tech_score: 0.71,
        news_score: 0.38,
        confidence: 0.74,
        expiry_hint: "weekly",
        updated_at: new Date().toISOString(),
        reason_kind: "hybrid",
        reason_summary: "EMA+RSI+news",
        reason_detail:
          "EMA9 > EMA21 on 15m; RSI(14) 58 — rising; Supertrend bullish; News: mild positive last 3h",
        reasons: ["EMA9 > EMA21 on 15m", "RSI(14) 58 — rising", "Supertrend bullish"],
      },
      {
        id: "local-bn",
        symbol: "BANKNIFTY",
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
        reason_detail: "EMA9 < EMA21; Close below VWAP; Bearish news cluster",
        reasons: ["EMA9 < EMA21", "Close below VWAP", "Bearish news cluster"],
      },
      {
        id: "local-tcs",
        symbol: "TCS",
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
        reason_detail: "News/TA conflict — hold",
        reasons: ["News/TA conflict — hold"],
      },
    ],
    total: 4,
    offset: 0,
    limit: 50,
    source: "local-mock",
  },
};
