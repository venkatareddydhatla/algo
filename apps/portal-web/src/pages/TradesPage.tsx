import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";

const PAGE_SIZE = 50;

type ClosedTrade = Record<string, unknown>;

export default function TradesPage() {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [offset, setOffset] = useState(0);
  /** Optional IST day YYYY-MM-DD for Day realized; empty = NSE session day default. */
  const [day, setDay] = useState("");
  const [closing, setClosing] = useState<string | null>(null);
  const [closeMsg, setCloseMsg] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setData(
        await api.trades({
          offset,
          limit: PAGE_SIZE,
          day: day.trim() || undefined,
        }),
      );
    } catch {
      setData({
        mode: "dry_run",
        instrument: "equity",
        execution: "dry_run",
        dry_run: true,
        positions: [],
        fills: [],
        fills_kind: "round_trip",
        fills_total: 0,
        fills_offset: offset,
        fills_limit: PAGE_SIZE,
        closed_trades: [],
        closed_trades_total: 0,
        day_ist: day.trim() || undefined,
        day_realized: 0,
        day_unrealized: 0,
        source: "local-empty",
      });
    }
  }, [offset, day]);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function onManualClose(symbol: string, side: string) {
    const sym = String(symbol || "").trim().toUpperCase();
    if (!sym || closing) return;
    const sideLabel = String(side || "").toLowerCase() === "short" ? "short" : "long";
    const verb = sideLabel === "short" ? "cover (buy)" : "sell";
    const ok = window.confirm(
      `Close ${sym} ${sideLabel} paper position now (${verb})?\nReason: manual_close · dry_run only`,
    );
    if (!ok) return;
    setClosing(sym);
    setCloseMsg(null);
    try {
      const res = await api.closeTrade(sym, {
        reason: "manual_close",
        confirm: true,
      });
      const pnl = res.realized_pnl != null ? Number(res.realized_pnl) : null;
      const pnlTxt =
        pnl == null || Number.isNaN(pnl)
          ? ""
          : ` · realized ₹${pnl >= 0 ? "+" : ""}${pnl.toFixed(2)}`;
      setCloseMsg(
        `Closed ${sym} ${String(res.order_side ?? verb)} @ ₹${Number(res.price ?? 0).toFixed(2)}${pnlTxt}`,
      );
      await reload();
    } catch (err) {
      setCloseMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setClosing(null);
    }
  }

  const positions = (data?.positions as Array<Record<string, unknown>>) || [];
  const trades =
    (data?.closed_trades as ClosedTrade[] | undefined) ||
    (data?.fills as ClosedTrade[]) ||
    [];
  const instrument = String(data?.instrument ?? "equity");
  const execution = String(data?.execution ?? data?.mode ?? "dry_run");
  const total = Number(
    data?.closed_trades_total ?? data?.fills_total ?? trades.length,
  );
  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const isRoundTrip =
    String(data?.fills_kind ?? "round_trip") === "round_trip" ||
    Boolean(data?.closed_trades);

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
      {closeMsg ? (
        <p className="notice" style={{ marginTop: 0 }}>
          {closeMsg}
        </p>
      ) : null}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Side</th>
              <th>Date &amp; Time (IST)</th>
              <th>Qty</th>
              <th>Avg</th>
              <th>uPnL</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {positions.length === 0 ? (
              <tr>
                <td colSpan={7} className="muted">
                  No open paper positions
                </td>
              </tr>
            ) : (
              positions.map((p, i) => {
                const sym = String(p.symbol ?? "");
                const side = String(p.side ?? "");
                const busy = closing === sym.toUpperCase();
                const label =
                  side.toLowerCase() === "short" ? "Cover" : "Sell/Close";
                return (
                  <tr key={i}>
                    <td>{sym}</td>
                    <td className="mono">{side || "—"}</td>
                    <td className="mono">
                      {fmtOpenEntryIst(
                        p.entry_ts_ist_display ?? p.opened_at_ist_display,
                        p.entry_ts ?? p.opened_at,
                      )}
                    </td>
                    <td className="mono">{String(p.qty)}</td>
                    <td className="mono">₹ {Number(p.avg_price).toFixed(2)}</td>
                    <td className="mono">
                      ₹ {Number(p.unrealized_pnl ?? 0).toFixed(0)}
                    </td>
                    <td>
                      <button
                        type="button"
                        className="danger"
                        disabled={Boolean(closing) || !sym}
                        onClick={() => void onManualClose(sym, side)}
                        title="Close this dry_run paper position now (manual_close)"
                      >
                        {busy ? "Closing…" : label}
                      </button>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
      <div className="section-head" style={{ marginTop: "1.5rem" }}>
        <h2>Closed trades</h2>
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
      <p className="muted" style={{ fontSize: "0.85rem", marginTop: 0 }}>
        One row per completed round-trip (entry + exit) · Asia/Kolkata (IST)
        {data?.raw_fills_total != null
          ? ` · ${Number(data.raw_fills_total)} raw fill legs in ledger`
          : ""}
      </p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Side</th>
              <th>Entry (IST)</th>
              <th>Exit (IST)</th>
              <th className="num-cell">Entry px</th>
              <th className="num-cell">Exit px</th>
              <th className="num-cell">PnL</th>
              <th>Reason</th>
              <th>dry_run</th>
            </tr>
          </thead>
          <tbody>
            {trades.length === 0 ? (
              <tr>
                <td colSpan={9} className="muted">
                  {isRoundTrip
                    ? "No closed paper trades yet — open legs stay above; worker equity dry-run round-trips appear here"
                    : "No paper fills yet — worker equity dry-run will appear here"}
                </td>
              </tr>
            ) : (
              trades.map((t, i) => {
                const pnl = t.pnl != null ? Number(t.pnl) : null;
                const side = String(t.side ?? "—");
                const sideClass =
                  side === "LONG" || side === "CE"
                    ? "action-BUY_CE_BIAS"
                    : side === "SHORT" || side === "PE"
                      ? "action-BUY_PE_BIAS"
                      : "";
                return (
                  <tr key={String(t.exit_fill_id ?? t.id ?? `${t.symbol}-${i}`)}>
                    <td>{String(t.symbol ?? "—")}</td>
                    <td className={`mono ${sideClass}`}>{side}</td>
                    <td className="mono">
                      {String(t.entry_ts_ist_display || t.entry_at_ist || "—")}
                    </td>
                    <td className="mono">
                      {String(t.exit_ts_ist_display || t.exit_at_ist || "—")}
                    </td>
                    <td className="mono num-cell">
                      {t.entry_price != null
                        ? `₹ ${Number(t.entry_price).toFixed(2)}`
                        : "—"}
                    </td>
                    <td className="mono num-cell">
                      {t.exit_price != null
                        ? `₹ ${Number(t.exit_price).toFixed(2)}`
                        : "—"}
                    </td>
                    <td
                      className={`mono num-cell ${
                        pnl == null
                          ? ""
                          : pnl >= 0
                            ? "action-BUY_CE_BIAS"
                            : "action-BUY_PE_BIAS"
                      }`}
                    >
                      {pnl == null
                        ? "—"
                        : `${pnl >= 0 ? "+" : ""}${pnl.toFixed(2)}`}
                    </td>
                    <td className="muted" title={String(t.reason || "")}>
                      {String(t.reason || t.exit_reason || "")}
                    </td>
                    <td className="mono">{t.dry_run === false ? "no" : "yes"}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
      <p className="pnl">
        Day realized
        {data?.day_ist ? ` (${String(data.day_ist)} IST)` : ""} ₹{" "}
        {Number(data?.day_realized ?? 0).toLocaleString()} · unrealized ₹{" "}
        {Number(data?.day_unrealized ?? 0).toLocaleString()}
      </p>
      <p className="muted" style={{ fontSize: "0.85rem", marginTop: 0 }}>
        Day realized = sum of closed round-trip PnL for the NSE cash session IST day
        (rolls at next 09:15 IST open; not UTC midnight).{" "}
        <label>
          Day{" "}
          <input
            type="date"
            value={day}
            onChange={(e) => {
              setOffset(0);
              setDay(e.target.value);
            }}
            style={{ marginLeft: "0.25rem" }}
          />
        </label>
        {day ? (
          <>
            {" · "}
            <button type="button" onClick={() => setDay("")}>
              Use session day
            </button>
          </>
        ) : null}
      </p>
      <p className="muted" style={{ fontSize: "0.85rem" }}>
        Dry-run fills are retained for review. Purge/archive only before live — see
        paper retention docs / admin purge / scripts/purge_paper_ledger.py.
      </p>
    </section>
  );
}

/** Prefer server IST display; fall back to formatting entry/opened ISO in IST. */
function fmtOpenEntryIst(
  display: unknown,
  iso: unknown,
): string {
  const disp = display != null ? String(display).trim() : "";
  if (disp && disp !== "—") return disp;
  const raw = iso != null ? String(iso).trim() : "";
  if (!raw) return "—";
  if (raw.endsWith(" IST")) return raw;
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return "—";
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(d);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")} ${get("hour")}:${get("minute")} IST`;
}
