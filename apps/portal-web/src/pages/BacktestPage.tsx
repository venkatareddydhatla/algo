import { FormEvent, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { BacktestResult, api } from "../api/client";

export default function BacktestPage() {
  const [params, setParams] = useSearchParams();
  const initialSymbol = (params.get("symbol") || "NIFTY").toUpperCase();
  const [symbol, setSymbol] = useState(initialSymbol);
  const [bars, setBars] = useState(200);
  const [fromDate, setFromDate] = useState(params.get("from") || "");
  const [toDate, setToDate] = useState(params.get("to") || "");
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [watchlistHints, setWatchlistHints] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const wl = await api.watchlist({ limit: 30 });
        if (!cancelled) {
          setWatchlistHints(wl.items.map((r) => r.symbol).slice(0, 12));
        }
      } catch {
        if (!cancelled) setWatchlistHints(["NIFTY", "BANKNIFTY", "RELIANCE", "TCS"]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const s = params.get("symbol");
    if (s) setSymbol(s.toUpperCase());
  }, [params]);

  const fills = useMemo(() => result?.fills ?? [], [result]);

  async function run(e?: FormEvent) {
    e?.preventDefault();
    setBusy(true);
    setError(null);
    const sym = symbol.trim().toUpperCase();
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("symbol", sym);
      if (fromDate) next.set("from", fromDate);
      else next.delete("from");
      if (toDate) next.set("to", toDate);
      else next.delete("to");
      return next;
    });
    try {
      const data = await api.backtestRun({
        symbol: sym,
        bars,
        from_date: fromDate || undefined,
        to_date: toDate || undefined,
        dry_run: true,
      });
      setResult(data);
    } catch (err) {
      setResult(null);
      setError(err instanceof Error ? err.message : "Backtest failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <div className="section-head">
        <h1>Backtest</h1>
        <p className="muted">
          Historical hybrid signal replay for one symbol — always <span className="mono">dry_run</span>.
          Spot-proxy PnL (not a full options chain).
        </p>
      </div>

      <form className="bt-form" onSubmit={run}>
        <label className="filter">
          <span className="muted">Symbol</span>
          <input
            className="mono"
            value={symbol}
            list="bt-symbols"
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            placeholder="NIFTY"
            required
          />
          <datalist id="bt-symbols">
            {watchlistHints.map((s) => (
              <option key={s} value={s} />
            ))}
          </datalist>
        </label>
        <label className="filter">
          <span className="muted">Bars</span>
          <input
            className="mono"
            type="number"
            min={50}
            max={2000}
            value={bars}
            onChange={(e) => setBars(Number(e.target.value) || 200)}
          />
        </label>
        <label className="filter">
          <span className="muted">From</span>
          <input
            className="mono"
            type="date"
            value={fromDate}
            onChange={(e) => setFromDate(e.target.value)}
          />
        </label>
        <label className="filter">
          <span className="muted">To</span>
          <input
            className="mono"
            type="date"
            value={toDate}
            onChange={(e) => setToDate(e.target.value)}
          />
        </label>
        <button type="submit" disabled={busy}>
          {busy ? "Running…" : "Run dry_run"}
        </button>
      </form>

      {watchlistHints.length > 0 ? (
        <p className="bt-hints muted">
          Quick pick:{" "}
          {watchlistHints.slice(0, 8).map((s) => (
            <button
              key={s}
              type="button"
              className="chip-link"
              onClick={() => {
                setSymbol(s);
                setParams((prev) => {
                  const next = new URLSearchParams(prev);
                  next.set("symbol", s);
                  return next;
                });
              }}
            >
              {s}
            </button>
          ))}
          · <Link to="/">Signals strip</Link>
        </p>
      ) : null}

      {error ? <p className="notice">{error}</p> : null}

      {result ? (
        <>
          <div className="bt-metrics" role="group" aria-label="Backtest summary">
            <Metric label="Trades" value={String(result.trades)} />
            <Metric label="Win rate" value={`${(result.win_rate * 100).toFixed(1)}%`} />
            <Metric
              label="PnL"
              value={`${result.pnl >= 0 ? "+" : ""}${result.pnl.toFixed(2)}`}
              tone={result.pnl >= 0 ? "ce" : "pe"}
            />
            <Metric label="Max DD" value={result.max_drawdown.toFixed(2)} />
            <Metric label="Profit factor" value={result.profit_factor.toFixed(2)} />
            <Metric label="Bars" value={`${result.bars_evaluated}/${result.bars}`} />
          </div>
          <p className="muted mono bt-meta">
            {result.symbol} · {result.mode} · source {result.source}
            {result.from_date ? ` · ${result.from_date}` : ""}
            {result.to_date ? ` → ${result.to_date}` : ""}
          </p>
          <h2>Fills</h2>
          <p className="muted bt-fills-note">Entry / exit times when the signal met · Asia/Kolkata (IST)</p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Side</th>
                  <th>Entry (IST)</th>
                  <th>Exit (IST)</th>
                  <th className="num-cell">Entry px</th>
                  <th className="num-cell">Exit px</th>
                  <th>Bars</th>
                  <th className="num-cell">PnL</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {fills.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="muted">
                      No fills in this window
                    </td>
                  </tr>
                ) : (
                  fills.map((f, i) => (
                    <tr key={`${f.entry_ts}-${i}`}>
                      <td className={f.side === "CE" ? "action-BUY_CE_BIAS" : "action-BUY_PE_BIAS"}>
                        {f.side}
                      </td>
                      <td className="mono">{fmtFillIst(f.entry_at_ist, f.entry_ts)}</td>
                      <td className="mono">{fmtFillIst(f.exit_at_ist, f.exit_ts)}</td>
                      <td className="mono num-cell">{f.entry_price.toFixed(2)}</td>
                      <td className="mono num-cell">{f.exit_price.toFixed(2)}</td>
                      <td className="mono">{f.bars_held}</td>
                      <td className={`mono num-cell ${f.pnl >= 0 ? "action-BUY_CE_BIAS" : "action-BUY_PE_BIAS"}`}>
                        {f.pnl >= 0 ? "+" : ""}
                        {f.pnl.toFixed(2)}
                      </td>
                      <td className="muted">{f.exit_reason}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
          {result.note ? <p className="muted notice">{result.note}</p> : null}
        </>
      ) : null}
    </section>
  );
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "ce" | "pe";
}) {
  return (
    <div className="bt-metric">
      <span className="muted">{label}</span>
      <span className={`mono ${tone === "ce" ? "action-BUY_CE_BIAS" : tone === "pe" ? "action-BUY_PE_BIAS" : ""}`}>
        {value}
      </span>
    </div>
  );
}

/** Prefer server ``*_at_ist`` display; fall back to formatting ISO ts in IST. */
function fmtFillIst(display: string | undefined, iso: string): string {
  if (display && display.trim()) return display;
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(d);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")} ${get("hour")}:${get("minute")} IST`;
}
