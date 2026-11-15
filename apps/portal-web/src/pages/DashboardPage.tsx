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
  }, [offset, filter]);

  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <section>
      <div className="section-head">
        <h1>Watchlist signals</h1>
        <p className="muted">
          NSE F&O universe ({total} symbols) — source: {source}. Worker scores the full
          watchlist in batches; actionable names sort to the top. Hover Reason for the
          decision rationale.
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
              <th>Reason</th>
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
                <td>
                  <ReasonCell row={r} />
                </td>
                <td className="mono">{fmt(r.hybrid_score)}</td>
                <td className="mono">{fmt(r.tech_score)}</td>
                <td className="mono">{fmt(r.news_score)}</td>
                <td>{r.expiry_hint ?? "-"}</td>
                <td className="muted">{new Date(r.updated_at).toLocaleTimeString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ReasonCell({ row }: { row: SignalRow }) {
  const summary = row.reason_summary || row.reason_kind || "-";
  const detail =
    row.reason_detail ||
    (row.reasons && row.reasons.length ? row.reasons.join("; ") : "No rationale available");
  const kind = (row.reason_kind || "neutral").toLowerCase();
  const sections = parseReasonSections(detail, kind);
  return (
    <button
      type="button"
      className="reason-chip"
      data-kind={kind}
      title={detail}
      aria-label={`Reason: ${kind}. ${summary}. ${detail}`}
    >
      <span className="reason-summary">{summary}</span>
      <span className="reason-popup" role="tooltip">
        <span className="reason-popup-kind mono">{kind.toUpperCase()}</span>
        {sections.map((sec) => (
          <span key={sec.title} className="reason-popup-section">
            {sec.title ? <span className="reason-popup-section-title">{sec.title}</span> : null}
            <span className="reason-popup-body">{sec.body}</span>
          </span>
        ))}
      </span>
    </button>
  );
}

/** Split API reason_detail into section blocks; drop News when kind is technical. */
function parseReasonSections(
  detail: string,
  kind: string,
): { title: string; body: string }[] {
  const blocks = detail
    .split(/\n\n+/)
    .map((b) => b.trim())
    .filter(Boolean);
  const parsed = blocks.map((block) => {
    const match = /^(Technical|News|Decision|Veto|Neutral):\s*([\s\S]*)$/i.exec(block);
    if (!match) {
      return { title: "", body: block };
    }
    return { title: match[1], body: match[2].trim() };
  });
  if (kind === "technical") {
    return parsed.filter((s) => !/^news$/i.test(s.title));
  }
  if (kind === "news") {
    return parsed.filter((s) => !/^technical$/i.test(s.title));
  }
  return parsed;
}

function sortLocal(items: SignalRow[]): SignalRow[] {
  return [...items].sort((a, b) => {
    const aAct = a.action !== "HOLD" ? 0 : 1;
    const bAct = b.action !== "HOLD" ? 0 : 1;
    if (aAct !== bAct) return aAct - bAct;
    const ah = Math.abs(a.hybrid_score);
    const bh = Math.abs(b.hybrid_score);
    if (ah !== bh) return bh - ah;
    if (a.confidence !== b.confidence) return b.confidence - a.confidence;
    return a.symbol.localeCompare(b.symbol);
  });
}

function fmt(n: number) {
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}`;
}
