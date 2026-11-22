import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { MOCK, SignalRow, api } from "../api/client";
import { MarketIndexStrip } from "../components/MarketIndexStrip";
import { fmtIstDateTime } from "../lib/ist";

const PAGE_SIZE = 50;

export default function DashboardPage() {
  const [params, setParams] = useSearchParams();
  const [rows, setRows] = useState<SignalRow[]>([]);
  const [source, setSource] = useState("loading");
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [filter, setFilter] = useState((params.get("symbol") || "").toUpperCase());
  const [highlight, setHighlight] = useState((params.get("symbol") || "").toUpperCase());
  const [customSymbols, setCustomSymbols] = useState<string[]>([]);
  const [customInput, setCustomInput] = useState("");
  const [customMsg, setCustomMsg] = useState("");
  const [customBusy, setCustomBusy] = useState(false);
  const [customTick, setCustomTick] = useState(0);

  useEffect(() => {
    const s = (params.get("symbol") || "").toUpperCase();
    if (s) {
      setFilter(s);
      setHighlight(s);
      setOffset(0);
    }
  }, [params]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.customWatchlist();
        if (!cancelled) {
          setCustomSymbols((data.items || []).map((r) => r.symbol));
        }
      } catch {
        if (!cancelled) setCustomSymbols([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [customTick]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.signals({
          offset,
          limit: PAGE_SIZE,
          symbol: filter.trim() || undefined,
        });
        if (!cancelled) {
          setRows(data.items);
          setTotal(data.total ?? data.items.length);
          setSource(data.source);
        }
      } catch {
        if (!cancelled) {
          const items = sortLocal(
            MOCK.signals.items.filter((r) =>
              filter.trim() ? r.symbol.includes(filter.trim().toUpperCase()) : true,
            ),
          );
          setRows(items);
          setTotal(items.length);
          setSource("local-mock (API offline)");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [offset, filter, customTick]);

  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  function selectSymbol(symbol: string) {
    const sym = symbol.toUpperCase();
    setFilter(sym);
    setHighlight(sym);
    setOffset(0);
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("symbol", sym);
      return next;
    });
  }

  function clearFilter() {
    setFilter("");
    setHighlight("");
    setOffset(0);
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("symbol");
      return next;
    });
  }

  async function addCustom() {
    const sym = customInput.trim().toUpperCase();
    if (!sym) {
      setCustomMsg("Enter an NSE equity ticker (e.g. VEDL)");
      return;
    }
    setCustomBusy(true);
    setCustomMsg("");
    try {
      const res = await api.addCustomSymbol(sym);
      setCustomMsg(res.message || `${sym} updated`);
      setCustomInput("");
      setCustomTick((n) => n + 1);
      setOffset(0);
    } catch (err) {
      setCustomMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setCustomBusy(false);
    }
  }

  async function removeCustom(sym: string) {
    setCustomBusy(true);
    setCustomMsg("");
    try {
      const res = await api.removeCustomSymbol(sym);
      setCustomMsg(res.message || `${sym} removed`);
      setCustomTick((n) => n + 1);
    } catch (err) {
      setCustomMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setCustomBusy(false);
    }
  }

  return (
    <section>
      <div className="section-head section-head-row">
        <div>
          <h1>Watchlist signals</h1>
          <p className="muted">
            Curated TradingView watchlist (~19) + custom equities ({total} symbols) — Reversal
            Pro Action (U1→BUY / D1→SELL, no HOLD) and Trend (BULLISH / BEARISH / NEUTRAL).
            Indices never appear as Action rows. Open dry-run trades keep BUY/SELL Action until
            close; Trend stays live. Source: {source}.
          </p>
        </div>
        <MarketIndexStrip />
      </div>

      <div className="custom-watchlist">
        <div className="custom-watchlist-head">
          <h2>Custom equities</h2>
          <p className="muted">
            Append NSE stock tickers on top of the curated ~19-name list. Same 15m bar-close
            scan + gates. Indices rejected. Persists in SQLite across restarts.
          </p>
        </div>
        <div className="toolbar custom-watchlist-toolbar">
          <label className="filter">
            <span className="muted">Add symbol</span>
            <input
              className="mono"
              value={customInput}
              placeholder="e.g. VEDL"
              disabled={customBusy}
              onChange={(e) => setCustomInput(e.target.value.toUpperCase())}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void addCustom();
                }
              }}
            />
          </label>
          <button type="button" disabled={customBusy} onClick={() => void addCustom()}>
            Add
          </button>
        </div>
        {customSymbols.length > 0 ? (
          <ul className="custom-symbol-list">
            {customSymbols.map((sym) => (
              <li key={sym}>
                <button
                  type="button"
                  className="sym-link mono"
                  onClick={() => selectSymbol(sym)}
                >
                  {sym}
                </button>
                <button
                  type="button"
                  className="danger"
                  disabled={customBusy}
                  onClick={() => void removeCustom(sym)}
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">No custom symbols yet.</p>
        )}
        {customMsg ? <p className="notice">{customMsg}</p> : null}
      </div>

      <div className="toolbar">
        <label className="filter">
          <span className="muted">Symbol</span>
          <input
            className="mono"
            value={filter}
            placeholder="e.g. BHARTIARTL / VEDL"
            onChange={(e) => {
              setOffset(0);
              const v = e.target.value.toUpperCase();
              setFilter(v);
              setHighlight(v);
              setParams((prev) => {
                const next = new URLSearchParams(prev);
                if (v.trim()) next.set("symbol", v.trim());
                else next.delete("symbol");
                return next;
              });
            }}
          />
        </label>
        {filter ? (
          <button type="button" onClick={clearFilter}>
            Clear
          </button>
        ) : null}
        <div className="pager">
          <button type="button" disabled={offset <= 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
            Prev
          </button>
          <span className="muted mono">
            {page}/{pages}
          </span>
          <button
            type="button"
            disabled={offset + PAGE_SIZE >= total}
            onClick={() => setOffset(offset + PAGE_SIZE)}
          >
            Next
          </button>
        </div>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Symbol</th>
              <th className="num-cell">Price</th>
              <th>Action</th>
              <th>Trend</th>
              <th>Updated</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr
                key={r.id}
                className={[
                  r.stub ? "stub-row" : "",
                  highlight && r.symbol === highlight ? "row-highlight" : "",
                ]
                  .filter(Boolean)
                  .join(" ") || undefined}
              >
                <td className="mono">
                  <button
                    type="button"
                    className="sym-link"
                    onClick={() => selectSymbol(r.symbol)}
                  >
                    {r.symbol}
                  </button>
                </td>
                <td className="mono num-cell" title={priceTitle(r)}>
                  {fmtPrice(r)}
                </td>
                <td>
                  <span className={`action action-${pineAction(r.action)}`}>
                    {pineAction(r.action)}
                  </span>
                </td>
                <td>
                  <span className={`trend trend-${pineTrend(r.trend)}`}>
                    {pineTrend(r.trend)}
                  </span>
                </td>
                <td className="muted mono">{fmtIstDateTime(r.updated_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function pineAction(action: string | undefined): "BUY" | "SELL" | "HOLD" {
  const a = (action || "HOLD").toUpperCase();
  if (a === "BUY" || a === "BUY_CE_BIAS" || a === "BUY_CE" || a === "LONG") return "BUY";
  if (a === "SELL" || a === "BUY_PE_BIAS" || a === "BUY_PE" || a === "SHORT") return "SELL";
  return "HOLD";
}

function pineTrend(trend: string | undefined): "BULLISH" | "BEARISH" | "NEUTRAL" {
  const t = (trend || "NEUTRAL").toUpperCase();
  if (t === "BULLISH" || t === "BULL" || t === "UP") return "BULLISH";
  if (t === "BEARISH" || t === "BEAR" || t === "DOWN") return "BEARISH";
  return "NEUTRAL";
}

function sortLocal(items: SignalRow[]): SignalRow[] {
  return [...items].sort((a, b) => {
    const aAct = pineAction(a.action) !== "HOLD" ? 0 : 1;
    const bAct = pineAction(b.action) !== "HOLD" ? 0 : 1;
    if (aAct !== bAct) return aAct - bAct;
    const ah = Math.abs(a.hybrid_score);
    const bh = Math.abs(b.hybrid_score);
    if (ah !== bh) return bh - ah;
    if (a.confidence !== b.confidence) return b.confidence - a.confidence;
    return a.symbol.localeCompare(b.symbol);
  });
}

function displayPrice(row: SignalRow): number | null {
  if (row.price != null && Number.isFinite(Number(row.price))) {
    return Number(row.price);
  }
  if (row.ltp != null && Number.isFinite(Number(row.ltp))) {
    return Number(row.ltp);
  }
  return null;
}

function fmtPrice(row: SignalRow): string {
  const px = displayPrice(row);
  return px != null ? `₹ ${px.toFixed(2)}` : "—";
}

function priceTitle(row: SignalRow): string | undefined {
  const src = row.price_source;
  if (!src) return undefined;
  if (src === "ltp") return "Live LTP";
  if (src === "candle_close") return "Last candle close";
  if (src === "last_known") return "Last known / display mark";
  return String(src);
}
