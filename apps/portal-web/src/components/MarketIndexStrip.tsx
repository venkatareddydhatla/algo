import { useEffect, useState } from "react";
import { IndexQuote, MarketIndicesPayload, api } from "../api/client";

const OPEN_POLL_MS = 15_000;
const CLOSE_POLL_MS = 120_000;

export function MarketIndexStrip() {
  const [payload, setPayload] = useState<MarketIndicesPayload | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function load() {
      try {
        const data = await api.marketIndices();
        if (cancelled) return;
        setPayload(data);
        const wait =
          (data.poll_hint_sec && data.poll_hint_sec > 0
            ? data.poll_hint_sec * 1000
            : data.market_status === "OPEN"
              ? OPEN_POLL_MS
              : CLOSE_POLL_MS) || OPEN_POLL_MS;
        timer = setTimeout(load, wait);
      } catch {
        if (cancelled) return;
        // Keep last good payload; retry slowly when API is down.
        timer = setTimeout(load, CLOSE_POLL_MS);
      }
    }

    void load();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  const status = payload?.market_status ?? "—";
  const open = status === "OPEN";
  const nifty = findIndex(payload, "NIFTY");
  const sensex = findIndex(payload, "SENSEX");

  return (
    <div
      className="market-index-strip"
      aria-label="Market status and index last prices"
      title={
        payload?.session
          ? `NSE cash ${payload.session.open_ist}–${payload.session.close_ist} ${payload.session.tz}`
          : undefined
      }
    >
      <span className={`market-status-badge ${open ? "is-open" : "is-close"}`}>
        {status}
      </span>
      <IndexLabel name="NIFTY" quote={nifty} marketOpen={open} />
      <IndexLabel name="SENSEX" quote={sensex} marketOpen={open} />
    </div>
  );
}

function findIndex(
  payload: MarketIndicesPayload | null,
  symbol: string,
): IndexQuote | undefined {
  return payload?.indices?.find((r) => r.symbol === symbol);
}

function IndexLabel({
  name,
  quote,
  marketOpen,
}: {
  name: string;
  quote?: IndexQuote;
  marketOpen: boolean;
}) {
  const px = quote?.last_price;
  const hasPx = px != null && Number.isFinite(Number(px));
  const change = quote?.change;
  const pct = quote?.change_pct;
  const up = change != null && change > 0;
  const down = change != null && change < 0;
  const stale = Boolean(quote?.stale) || !marketOpen;

  return (
    <span
      className="market-index-label mono"
      title={
        hasPx
          ? `${name} ${stale ? "last known" : "live"} · source=${quote?.source ?? "-"}`
          : `${name} — no quote yet`
      }
    >
      <span className="market-index-name">{name}</span>
      <span className="market-index-px num-cell">
        {hasPx ? Number(px).toLocaleString("en-IN", { maximumFractionDigits: 2 }) : "—"}
      </span>
      {hasPx && change != null && Number.isFinite(Number(change)) ? (
        <span
          className={`market-index-chg ${up ? "is-up" : ""} ${down ? "is-down" : ""}`}
        >
          {up ? "+" : ""}
          {Number(change).toFixed(2)}
          {pct != null && Number.isFinite(Number(pct))
            ? ` (${up ? "+" : ""}${Number(pct).toFixed(2)}%)`
            : ""}
        </span>
      ) : null}
      {stale && hasPx ? <span className="market-index-stale">stale</span> : null}
    </span>
  );
}
