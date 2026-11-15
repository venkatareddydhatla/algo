import { Link } from "react-router-dom";
import { SignalRow } from "../api/client";

type Props = {
  ce: SignalRow[];
  pe: SignalRow[];
  selected?: string;
  onSelect: (symbol: string) => void;
  source?: string;
};

export function HybridStrip({ ce, pe, selected, onSelect, source }: Props) {
  return (
    <div className="hybrid-strip" aria-label="Top hybrid CE and PE candidates">
      <StripSide
        title="Top CE"
        tone="ce"
        rows={ce}
        selected={selected}
        onSelect={onSelect}
      />
      <StripSide
        title="Top PE"
        tone="pe"
        rows={pe}
        selected={selected}
        onSelect={onSelect}
      />
      {source ? (
        <p className="hybrid-strip-meta muted">
          Hybrid strip · prefers <span className="mono">reason_kind=hybrid</span> · {source}
        </p>
      ) : null}
    </div>
  );
}

function StripSide({
  title,
  tone,
  rows,
  selected,
  onSelect,
}: {
  title: string;
  tone: "ce" | "pe";
  rows: SignalRow[];
  selected?: string;
  onSelect: (symbol: string) => void;
}) {
  return (
    <div className={`hybrid-side hybrid-side-${tone}`}>
      <div className="hybrid-side-label">{title}</div>
      <div className="hybrid-chips">
        {rows.length === 0 ? (
          <span className="muted">None</span>
        ) : (
          rows.map((r) => {
            const active = selected === r.symbol;
            return (
              <span key={r.id || r.symbol} className="hybrid-chip-wrap">
                <button
                  type="button"
                  className={`hybrid-chip ${active ? "is-active" : ""}`}
                  onClick={() => onSelect(r.symbol)}
                  title={`${r.symbol} ${r.action} hybrid=${r.hybrid_score.toFixed(2)} (${r.reason_kind || "-"})`}
                >
                  <span className="mono">{r.symbol}</span>
                  <span className="hybrid-chip-score mono">
                    {r.hybrid_score > 0 ? "+" : ""}
                    {r.hybrid_score.toFixed(2)}
                  </span>
                  <span className="hybrid-chip-kind muted">{(r.reason_kind || "").toUpperCase()}</span>
                </button>
                <Link
                  className="hybrid-bt-link muted"
                  to={`/backtest?symbol=${encodeURIComponent(r.symbol)}`}
                  title={`Backtest ${r.symbol}`}
                >
                  BT
                </Link>
              </span>
            );
          })
        )}
      </div>
    </div>
  );
}
