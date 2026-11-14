import { useEffect, useState } from "react";
import { api } from "../api/client";

export default function AdminPage() {
  const [cfg, setCfg] = useState<Record<string, unknown> | null>(null);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    (async () => {
      try {
        setCfg(await api.config());
      } catch {
        setCfg({
          trading: { mode: "dry_run", halt: false, symbols: ["NIFTY", "BANKNIFTY", "RELIANCE", "TCS"] },
          v1_note: "API offline — showing defaults",
        });
      }
    })();
  }, []);

  const trading = (cfg?.trading as Record<string, unknown>) || {};

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
          <h2>Symbols</h2>
          <p className="mono">{((trading.symbols as string[]) || []).join(", ")}</p>
          <h2>Kill switch</h2>
          <p>halt={String(trading.halt ?? false)} — set TRADING_HALT=true in env for process halt</p>
        </div>
      </div>
    </section>
  );
}
