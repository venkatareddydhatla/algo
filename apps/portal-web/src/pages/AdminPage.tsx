import { useEffect, useState } from "react";
import { api } from "../api/client";

function formatMaxActive(value: unknown): string {
  if (value === null || value === undefined || value === "" || Number(value) <= 0) {
    return "unlimited (0)";
  }
  return String(value);
}

export default function AdminPage() {
  const [cfg, setCfg] = useState<Record<string, unknown> | null>(null);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    (async () => {
      try {
        setCfg(await api.config());
      } catch {
        setCfg({
          trading: {
            mode: "dry_run",
            halt: false,
            symbols_demo: ["NIFTY", "BANKNIFTY", "RELIANCE", "TCS"],
            universe: "nse_fno",
            symbols_watchlist_count: 0,
            symbols_active_count: 0,
            symbols_preview: ["NIFTY", "BANKNIFTY", "RELIANCE", "TCS"],
          },
          v1_note: "API offline — showing defaults",
        });
      }
    })();
  }, []);

  const trading = (cfg?.trading as Record<string, unknown>) || {};
  const preview = (trading.symbols_preview as string[]) || (trading.symbols as string[]) || [];

  async function requestMode(mode: string) {
    try {
      const res = await fetch(
        `${import.meta.env.VITE_API_BASE?.replace(/\/$/, "") || ""}/api/v1/admin/trading-mode`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mode, confirm_live: mode === "live" }),
        },
      );
      const body = await res.json();
      setMsg(body.message || JSON.stringify(body));
    } catch {
      setMsg("Could not reach API — mode unchanged. Keep dry_run.");
    }
  }

  return (
    <section>
      <div className="section-head">
        <h1>Admin · configuration</h1>
        <p className="muted">{String(cfg?.v1_note ?? "")}</p>
        {cfg?.universe_note ? <p className="muted">{String(cfg.universe_note)}</p> : null}
      </div>
      <div className="admin-grid">
        <div>
          <h2>Trading mode</h2>
          <p className="mono">current: {String(trading.mode ?? "dry_run")}</p>
          <div className="btn-row">
            <button type="button" onClick={() => requestMode("dry_run")}>
              dry_run
            </button>
            <button type="button" onClick={() => requestMode("demo")}>
              demo
            </button>
            <button type="button" className="danger" onClick={() => requestMode("live")}>
              live (gated)
            </button>
          </div>
          {msg ? <p className="notice">{msg}</p> : null}
        </div>
        <div>
          <h2>Watchlist / universe</h2>
          <p className="mono">
            universe={String(trading.universe ?? "-")} · watchlist=
            {String(trading.symbols_watchlist_count ?? preview.length)} · active_scan=
            {String(trading.symbols_active_count ?? "-")} / max=
            {formatMaxActive(trading.max_active_symbols)} · custom=
            {String(
              Array.isArray(trading.symbols_custom)
                ? (trading.symbols_custom as string[]).length
                : "-",
            )}
          </p>
          <p className="muted">Preview (first symbols):</p>
          <p className="mono wrap">{preview.slice(0, 40).join(", ")}</p>
          <p className="muted">
            Full list: GET /api/v1/watchlist. Add customs on Signals (POST
            /api/v1/watchlist/custom). Demo set:{" "}
            {((trading.symbols_demo as string[]) || []).join(", ") || "NIFTY, BANKNIFTY, RELIANCE, TCS"}
          </p>
          <h2>Kill switch</h2>
          <p>halt={String(trading.halt ?? false)} — set TRADING_HALT=true in env for process halt</p>
        </div>
      </div>
    </section>
  );
}
