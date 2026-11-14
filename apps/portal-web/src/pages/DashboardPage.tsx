import { useEffect, useState } from "react";
import { MOCK, SignalRow, api } from "../api/client";

const PAGE_SIZE = 50;

export default function DashboardPage() {
  const [rows, setRows] = useState<SignalRow[]>([]);
  const [source, setSource] = useState("loading");
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [filter, setFilter] = useState("");

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
          const items = MOCK.signals.items.filter((r) =>
            filter.trim() ? r.symbol.includes(filter.trim().toUpperCase()) : true,
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
  }, [offset, filter]);

  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <section>
      <div className="section-head">
        <h1>Watchlist signals</h1>
        <p className="muted">
          NSE F&O universe ({total} shown scope) — source: {source}. Worker scans a capped
          active subset; full list is watchable here.
        </p>
      </div>
      <div className="toolbar">
        <label className="filter">
          <span className="muted">Symbol</span>
          <input
            className="mono"
            value={filter}
            placeholder="e.g. NIFTY / RELIANCE"
            onChange={(e) => {
              setOffset(0);
              setFilter(e.target.value.toUpperCase());
            }}
          />
        </label>
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
              <tr key={r.id} className={r.stub ? "stub-row" : undefined}>
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
