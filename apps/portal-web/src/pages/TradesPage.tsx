import { useEffect, useState } from "react";
import { api } from "../api/client";

export default function TradesPage() {
  const [data, setData] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    (async () => {
      try {
        setData(await api.trades());
      } catch {
        setData({
          mode: "dry_run",
          positions: [],
          fills: [],
          day_realized: 0,
          day_unrealized: 0,
          source: "local-empty",
        });
      }
    })();
  }, []);

  const positions = (data?.positions as Array<Record<string, unknown>>) || [];
  const fills = (data?.fills as Array<Record<string, unknown>>) || [];

  return (
    <section>
      <div className="section-head">
        <h1>Trades & P&L</h1>
        <p className="muted">Paper ledger · mode {(data?.mode as string) || "dry_run"}</p>
      </div>
      <h2>Open positions</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Contract</th>
              <th>Qty</th>
              <th>Avg</th>
              <th>uPnL</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((p, i) => (
              <tr key={i}>
                <td>{String(p.symbol)}</td>
                <td className="mono">{String(p.qty)}</td>
                <td className="mono">₹ {Number(p.avg_price).toFixed(2)}</td>
                <td className="mono">₹ {Number(p.unrealized_pnl).toFixed(0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h2>Fills</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Status</th>
              <th>Side</th>
              <th>Symbol</th>
              <th>Qty</th>
              <th>Price</th>
            </tr>
          </thead>
          <tbody>
            {fills.map((f, i) => (
              <tr key={i}>
                <td>{String(f.status)}</td>
                <td>{String(f.side)}</td>
                <td>{String(f.symbol)}</td>
                <td className="mono">{String(f.qty)}</td>
                <td className="mono">₹ {Number(f.price).toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="pnl">
        Day realized ₹ {Number(data?.day_realized ?? 0).toLocaleString()} · unrealized ₹{" "}
        {Number(data?.day_unrealized ?? 0).toLocaleString()}
      </p>
    </section>
  );
}
