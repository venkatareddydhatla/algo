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
    <div className="hybrid-strip" aria-label="Top Pine BUY and SELL candidates">
      <StripSide
        title="Top BUY"
        tone="ce"
        rows={ce}
        selected={selected}
        onSelect={onSelect}
      />
      <StripSide
        title="Top SELL"
        tone="pe"
        rows={pe}
        selected={selected}
        onSelect={onSelect}
      />
      {source ? (
        <p className="hybrid-strip-meta muted">
          Pine strip · Action from <span className="mono">buy_signal / sell_signal</span> ·{" "}
          {source}
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
                  title={`${r.symbol} ${r.action} (${r.reason_summary || r.reason_kind || "pine"})`}
                >
                  <span className="mono">{r.symbol}</span>
                  <span className="hybrid-chip-score mono">{r.action}</span>
                  <span className="hybrid-chip-kind muted">
                    {(r.reason_summary || r.reason_kind || "pine").toUpperCase()}
                  </span>
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
