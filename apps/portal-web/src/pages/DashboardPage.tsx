import { useEffect, useState } from "react";
import { MOCK, SignalRow, api } from "../api/client";

export default function DashboardPage() {
  const [rows, setRows] = useState<SignalRow[]>([]);
  const [source, setSource] = useState("loading");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.signals();
        if (!cancelled) {
          setRows(data.items);
          setSource(data.source);
        }
      } catch {
        if (!cancelled) {
          setRows(MOCK.signals.items);
          setSource("local-mock (API offline)");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section>
      <div className="section-head">
        <h1>Watchlist signals</h1>
        <p className="muted">NIFTY · BANKNIFTY · RELIANCE · TCS — source: {source}</p>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Action</th>
              <th>Hybrid</th>
              <th>Tech</th>
              <th>News</th>
              <th>Expiry</th>
              <th>Updated</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td className="mono">{r.symbol}</td>
                <td>
                  <span className={`action action-${r.action}`}>{r.action}</span>
                </td>
                <td className="mono">{fmt(r.hybrid_score)}</td>
                <td className="mono">{fmt(r.tech_score)}</td>
                <td className="mono">{fmt(r.news_score)}</td>
                <td>{r.expiry_hint ?? "—"}</td>
                <td className="muted">{new Date(r.updated_at).toLocaleTimeString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function fmt(n: number) {
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}`;
}
