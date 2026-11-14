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

export const api = {
  signals: () => getJson<{ items: SignalRow[]; source: string }>("/api/v1/signals"),
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
      },
    ],
    source: "local-mock",
  },
};
