import { useEffect, useState } from "react";
import { api } from "../api/client";

const PAGE_SIZE = 50;

export default function TradesPage() {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    (async () => {
      try {
        setData(await api.trades({ offset, limit: PAGE_SIZE }));
      } catch {
        setData({
          mode: "dry_run",
          instrument: "equity",
          execution: "dry_run",
          dry_run: true,
          positions: [],
          fills: [],
          fills_total: 0,
          fills_offset: offset,
          fills_limit: PAGE_SIZE,
          day_realized: 0,
          day_unrealized: 0,
          source: "local-empty",
        });
      }
    })();
  }, [offset]);

  const positions = (data?.positions as Array<Record<string, unknown>>) || [];
  const fills = (data?.fills as Array<Record<string, unknown>>) || [];
  const instrument = String(data?.instrument ?? "equity");
  const execution = String(data?.execution ?? data?.mode ?? "dry_run");
  const total = Number(data?.fills_total ?? fills.length);
  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <section>
      <div className="section-head">
        <h1>Trades & P&L</h1>
        <p className="muted">
          Paper ledger · {instrument} · {execution}
          {data?.dry_run === false ? "" : " · dry_run"}
          {data?.source ? ` · ${String(data.source)}` : ""}
          {" · history kept (not today-only)"}
        </p>
      </div>
      <h2>Open positions</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Side</th>
              <th>Qty</th>
              <th>Avg</th>
              <th>uPnL</th>
            </tr>
          </thead>
          <tbody>
            {positions.length === 0 ? (
              <tr>
                <td colSpan={5} className="muted">
                  No open paper positions
                </td>
              </tr>
            ) : (
              positions.map((p, i) => (
                <tr key={i}>
                  <td>{String(p.symbol)}</td>
                  <td className="mono">{String(p.side ?? "—")}</td>
                  <td className="mono">{String(p.qty)}</td>
                  <td className="mono">₹ {Number(p.avg_price).toFixed(2)}</td>
                  <td className="mono">₹ {Number(p.unrealized_pnl ?? 0).toFixed(0)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      <div className="section-head" style={{ marginTop: "1.5rem" }}>
        <h2>Fills</h2>
        <div className="pager">
          <button type="button" disabled={offset <= 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
            Prev
          </button>
          <span className="muted mono">
            {page}/{pages} · {total} total
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
              <th>Time (IST)</th>
              <th>Status</th>
              <th>Side</th>
              <th>Symbol</th>
              <th>Qty</th>
              <th>Price</th>
              <th>Reason</th>
              <th>dry_run</th>
            </tr>
          </thead>
          <tbody>
            {fills.length === 0 ? (
              <tr>
                <td colSpan={8} className="muted">
                  No paper fills yet — worker equity dry-run will appear here
                </td>
              </tr>
            ) : (
              fills.map((f, i) => (
                <tr key={String(f.id ?? i)}>
                  <td className="mono">
                    {String(f.ts_ist_display || f.ts_ist || f.ts || "—")}
                  </td>
                  <td>{String(f.status)}</td>
                  <td className="mono">{String(f.side)}</td>
                  <td>{String(f.symbol)}</td>
                  <td className="mono">{String(f.qty)}</td>
                  <td className="mono">₹ {Number(f.price).toFixed(2)}</td>
                  <td className="muted">{String(f.reason || f.signal_action || "")}</td>
                  <td className="mono">{f.dry_run === false ? "no" : "yes"}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      <p className="pnl">
        Day realized ₹ {Number(data?.day_realized ?? 0).toLocaleString()} · unrealized ₹{" "}
        {Number(data?.day_unrealized ?? 0).toLocaleString()}
      </p>
      <p className="muted" style={{ fontSize: "0.85rem" }}>
        Dry-run fills are retained for review. Purge/archive only before live — see
        paper retention docs / admin purge / scripts/purge_paper_ledger.py.
      </p>
    </section>
  );
}
