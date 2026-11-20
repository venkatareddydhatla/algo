const API_BASE = import.meta.env.VITE_API_BASE?.replace(/\/$/, "") || "";

/** FastAPI may return detail as string or validation-error array. */
export function formatApiDetail(detail: unknown, fallback: string): string {
  if (detail == null) return fallback;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object") {
          const row = item as { msg?: unknown; loc?: unknown };
          const loc = Array.isArray(row.loc)
            ? row.loc.filter((x) => x !== "body").join(".")
            : "";
          const msg = row.msg != null ? String(row.msg) : "";
          return loc && msg ? `${loc}: ${msg}` : msg || JSON.stringify(item);
        }
        return String(item);
      })
      .filter(Boolean);
    if (parts.length) return parts.join("; ");
  }
  if (typeof detail === "object") {
    try {
      return JSON.stringify(detail);
    } catch {
      /* ignore */
    }
  }
  return fallback;
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const err = (await res.json()) as { detail?: unknown };
      detail = formatApiDetail(err.detail, detail);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

async function deleteJson<T>(path: string, opts?: { timeoutMs?: number }): Promise<T> {
  const timeoutMs = opts?.timeoutMs ?? 25_000;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method: "DELETE",
      headers: { Accept: "application/json" },
      signal: ctrl.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new Error(
        `Request timed out after ${Math.round(timeoutMs / 1000)}s — is the API on :8000 running the latest tip?`,
      );
    }
    const msg = err instanceof Error ? err.message : String(err);
    throw new Error(msg);
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const err = (await res.json()) as { detail?: unknown };
      detail = formatApiDetail(err.detail, detail);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

async function postJson<T>(
  path: string,
  body: unknown,
  opts?: { timeoutMs?: number },
): Promise<T> {
  const timeoutMs = opts?.timeoutMs ?? 25_000;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new Error(
        `Request timed out after ${Math.round(timeoutMs / 1000)}s — is the API on :8000 running the latest tip?`,
      );
    }
    const msg = err instanceof Error ? err.message : String(err);
    throw new Error(
      msg.toLowerCase().includes("fetch")
        ? `Network/CORS error calling ${path} (API_BASE=${API_BASE || "(vite proxy)"}). Is API on :8000 up? ${msg}`
        : msg,
    );
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const err = (await res.json()) as { detail?: unknown };
      detail = formatApiDetail(err.detail, detail);
    } catch {
      /* ignore */
    }
    if (res.status === 404) {
      detail = `${detail} — POST ${path} missing; restart API after pulling tip (stop-native then start-native)`;
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
  source?: "curated" | "custom" | string;
  custom?: boolean;
};

export type CustomWatchlistPayload = {
  ok?: boolean;
  status?: string;
  symbol?: string;
  message?: string;
  instrument_key?: string | null;
  customs?: string[];
  custom_symbols?: string[];
  watchlist_total?: number;
  active_scan_count?: number;
  items?: Array<{
    symbol: string;
    instrument_key?: string | null;
    active_scan?: boolean;
    on_curated?: boolean;
    source?: string;
  }>;
  total?: number;
  base_count?: number;
  note?: string;
  kv_key?: string;
  source?: string;
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
  base_count?: number;
  custom_count?: number;
  custom_symbols?: string[];
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
  customWatchlist: () => getJson<CustomWatchlistPayload>("/api/v1/watchlist/custom"),
  addCustomSymbol: (symbol: string) =>
    postJson<CustomWatchlistPayload>("/api/v1/watchlist/custom", {
      symbol: String(symbol || "")
        .trim()
        .toUpperCase(),
    }),
  removeCustomSymbol: (symbol: string) => {
    const sym = String(symbol || "")
      .trim()
      .toUpperCase();
    if (!sym) {
      return Promise.reject(new Error("symbol required"));
    }
    return deleteJson<CustomWatchlistPayload>(
      `/api/v1/watchlist/custom/${encodeURIComponent(sym)}`,
    );
  },
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
  ) => {
    const sym = String(symbol || "")
      .trim()
      .toUpperCase();
    if (!sym) {
      return Promise.reject(new Error("symbol required"));
    }
    return postJson<Record<string, unknown>>(
      `/api/v1/trades/${encodeURIComponent(sym)}/close`,
      {
        reason: body?.reason ?? "manual_close",
        confirm: body?.confirm ?? true,
      },
      { timeoutMs: 25_000 },
    );
  },
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
