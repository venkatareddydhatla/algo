"""Equity dry-run paper ledger (SQLite local profile).

Retention policy (see docs/options-portal-paper-retention.md):

- **dry_run / demo:** never auto-delete fills or open positions. Keep history
  for the review week (and beyond) until an explicit purge/archive.
- **Before live:** call ``archive_and_purge`` (API or script) with confirm.
- **After live:** ``run_live_retention`` may drop fills older than
  ``live_min_retain_days`` (default ≥90). Never runs while mode is dry_run/demo.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from options_portal_domain.equity import (
    EquityOrderIntent,
    PositionSide,
    realized_equity_pnl,
    unrealized_equity_pnl,
)
from options_portal_domain.round_trips import (
    closed_round_trip_fill_ids,
    pair_round_trip_trades,
    sort_closed_trades_newest_first,
)
from options_portal_domain.tz import ist
from options_portal_domain.types import OrderResult, TradingMode
from options_portal_persistence.db import _sqlite_path_from_url, database_url, is_local_profile
from options_portal_persistence.sqlite_schema import apply_paper_ledger_schema

logger = logging.getLogger(__name__)

# Closing an open position removes the row from paper_positions — that is not
# fill/history deletion. Fill rows are append-only until explicit purge/retention.
_FILL_ROW_COLS = (
    "id, client_order_id, broker_order_id, mode, symbol, side, qty, price, "
    "status, reason, signal_action, action_kind, position_side, dry_run, "
    "realized_pnl, ts_utc, ts_ist, ts_ist_display"
)


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _format_ist(dt: datetime) -> tuple[str, str]:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(ist())
    return local.isoformat(), local.strftime("%Y-%m-%d %H:%M IST")


def _position_entry_fields(opened_at: Any) -> dict[str, Any]:
    """Wire ``opened_at`` / ``entry_ts`` + IST display for Open positions UI.

    ``opened_at`` is stored as UTC ISO when the leg is entered. Display matches
    Closed trades Entry (IST): ``YYYY-MM-DD HH:MM IST``.
    """
    raw = str(opened_at).strip() if opened_at is not None else ""
    if not raw:
        return {
            "opened_at": None,
            "entry_ts": None,
            "opened_at_ist_display": "—",
            "entry_ts_ist_display": "—",
        }
    if raw.endswith(" IST"):
        return {
            "opened_at": raw,
            "entry_ts": raw,
            "opened_at_ist_display": raw,
            "entry_ts_ist_display": raw,
        }
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return {
            "opened_at": raw,
            "entry_ts": raw,
            "opened_at_ist_display": "—",
            "entry_ts_ist_display": "—",
        }
    ts_ist, ts_display = _format_ist(dt)
    return {
        "opened_at": raw,
        "entry_ts": raw,
        "opened_at_ist": ts_ist,
        "opened_at_ist_display": ts_display,
        "entry_ts_ist_display": ts_display,
    }


def _row_to_fill(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "client_order_id": row["client_order_id"],
        "broker_order_id": row["broker_order_id"],
        "ts": row["ts_utc"],
        "ts_ist": row["ts_ist"],
        "ts_ist_display": row["ts_ist_display"],
        "status": row["status"],
        "side": row["side"],
        "symbol": row["symbol"],
        "qty": int(row["qty"]),
        "price": float(row["price"]),
        "reason": row["reason"] or "",
        "signal_action": row["signal_action"],
        "action_kind": row["action_kind"],
        "position_side": row["position_side"],
        "dry_run": bool(row["dry_run"]),
        "realized_pnl": row["realized_pnl"],
        "mode": row["mode"],
    }


class PaperTradeStore:
    """Persist simulated equity fills + open positions for Trades UI."""

    def __init__(self, db_url: str | None = None) -> None:
        self._url = db_url or database_url()
        self._path: Path | None = None
        if is_local_profile() or self._url.lower().startswith("sqlite"):
            path = _sqlite_path_from_url(self._url)
            if str(path) != ":memory:" and path.name != ":memory:":
                path.parent.mkdir(parents=True, exist_ok=True)
            self._path = path
            self._ensure_schema()
        else:
            logger.warning(
                "PaperTradeStore: non-SQLite profile — paper ledger disabled "
                "(use PERSISTENCE_PROFILE=local for native dry-run)"
            )

    @property
    def available(self) -> bool:
        return self._path is not None

    def _connect(self) -> sqlite3.Connection:
        assert self._path is not None
        if str(self._path) == ":memory:" or self._path.name == ":memory:":
            conn = sqlite3.connect(
                "file:options_portal_paper?mode=memory&cache=shared",
                uri=True,
                check_same_thread=False,
            )
        else:
            conn = sqlite3.connect(str(self._path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            apply_paper_ledger_schema(conn)

    def get_position(self, symbol: str, *, mode: TradingMode = "dry_run") -> dict[str, Any] | None:
        if not self.available:
            return None
        with self._connect() as conn:
            apply_paper_ledger_schema(conn)
            row = conn.execute(
                "SELECT * FROM paper_positions WHERE mode = ? AND symbol = ?",
                (mode, symbol.upper()),
            ).fetchone()
        if not row:
            return None
        return dict(row)

    def list_positions(self, *, mode: TradingMode = "dry_run") -> list[dict[str, Any]]:
        if not self.available:
            return []
        with self._connect() as conn:
            apply_paper_ledger_schema(conn)
            rows = conn.execute(
                "SELECT * FROM paper_positions WHERE mode = ? ORDER BY symbol",
                (mode,),
            ).fetchall()
        return [dict(r) for r in rows]

    def open_symbols(self, *, mode: TradingMode = "dry_run") -> list[str]:
        return [str(p["symbol"]) for p in self.list_positions(mode=mode)]

    def day_realized_pnl(self, *, mode: TradingMode = "dry_run") -> float:
        if not self.available:
            return 0.0
        # IST calendar day
        now_ist = _utc_now().astimezone(ist())
        day_prefix = now_ist.strftime("%Y-%m-%d")
        with self._connect() as conn:
            apply_paper_ledger_schema(conn)
            row = conn.execute(
                """
                SELECT COALESCE(SUM(realized_pnl), 0) AS pnl
                FROM paper_fills
                WHERE mode = ? AND realized_pnl IS NOT NULL
                  AND ts_ist_display LIKE ?
                """,
                (mode, f"{day_prefix}%"),
            ).fetchone()
        return float(row["pnl"] if row else 0.0)

    def portfolio_snapshot(self, *, mode: TradingMode = "dry_run") -> dict[str, Any]:
        return {
            "open_symbols": self.open_symbols(mode=mode),
            "day_realized_pnl": self.day_realized_pnl(mode=mode),
            "positions": self.list_positions(mode=mode),
        }

    def record_simulated_fill(
        self,
        intent: EquityOrderIntent,
        order: OrderResult,
        *,
        mode: TradingMode = "dry_run",
    ) -> dict[str, Any]:
        """Apply a simulated equity fill to positions + fills tables."""
        if not self.available:
            return {"persisted": False, "reason": "store unavailable"}
        if order.status not in {"simulated", "filled"}:
            return {"persisted": False, "reason": f"status={order.status}"}

        now = _utc_now()
        ts_utc = now.isoformat()
        ts_ist, ts_display = _format_ist(now)
        fill_id = f"fill-{uuid.uuid4().hex[:12]}"
        realized: float | None = None
        resulting: PositionSide | None = intent.resulting_position

        with self._connect() as conn:
            apply_paper_ledger_schema(conn)
            pos = conn.execute(
                "SELECT * FROM paper_positions WHERE mode = ? AND symbol = ?",
                (mode, intent.symbol),
            ).fetchone()

            if intent.action_kind in {"exit", "flip_close"}:
                if pos:
                    avg = float(pos["avg_price"])
                    qty = int(intent.qty)
                    exit_mark = float(intent.limit_price)
                    realized = realized_equity_pnl(
                        str(pos["side"]), avg, exit_mark, qty
                    )
                    conn.execute(
                        "DELETE FROM paper_positions WHERE mode = ? AND symbol = ?",
                        (mode, intent.symbol),
                    )
                resulting = None
            else:
                # entry / flip_open
                side = intent.resulting_position or (
                    "long" if intent.side == "BUY" else "short"
                )
                conn.execute(
                    """
                    INSERT INTO paper_positions
                      (mode, symbol, side, qty, avg_price, opened_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(mode, symbol) DO UPDATE SET
                      side=excluded.side,
                      qty=excluded.qty,
                      avg_price=excluded.avg_price,
                      updated_at=excluded.updated_at
                    """,
                    (
                        mode,
                        intent.symbol,
                        side,
                        int(intent.qty),
                        float(intent.limit_price),
                        ts_utc,
                        ts_utc,
                    ),
                )
                resulting = side  # type: ignore[assignment]

            dry_run = 1 if mode in {"dry_run", "demo"} else 0
            conn.execute(
                """
                INSERT INTO paper_fills
                  (id, client_order_id, broker_order_id, mode, symbol, side, qty, price,
                   status, reason, signal_action, action_kind, position_side, dry_run,
                   realized_pnl, ts_utc, ts_ist, ts_ist_display)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fill_id,
                    order.client_order_id,
                    order.broker_order_id,
                    mode,
                    intent.symbol,
                    intent.side,
                    int(intent.qty),
                    float(intent.limit_price),
                    order.status,
                    intent.reason,
                    intent.signal_action,
                    intent.action_kind,
                    resulting,
                    dry_run,
                    realized,
                    ts_utc,
                    ts_ist,
                    ts_display,
                ),
            )
            conn.commit()

        return {
            "persisted": True,
            "fill_id": fill_id,
            "realized_pnl": realized,
            "ts_ist_display": ts_display,
            "dry_run": True,
        }

    def count_fills(self, *, mode: TradingMode = "dry_run") -> int:
        if not self.available:
            return 0
        with self._connect() as conn:
            apply_paper_ledger_schema(conn)
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM paper_fills WHERE mode = ?",
                (mode,),
            ).fetchone()
        return int(row["n"] if row else 0)

    def list_fills(
        self,
        *,
        mode: TradingMode = "dry_run",
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return fills newest-first (full history — not today-only)."""
        if not self.available:
            return []
        lim = max(1, min(int(limit), 500))
        off = max(0, int(offset))
        with self._connect() as conn:
            apply_paper_ledger_schema(conn)
            rows = conn.execute(
                f"""
                SELECT {_FILL_ROW_COLS} FROM paper_fills
                WHERE mode = ?
                ORDER BY ts_utc DESC
                LIMIT ? OFFSET ?
                """,
                (mode, lim, off),
            ).fetchall()
        return [_row_to_fill(row) for row in rows]

    def list_all_fills(
        self,
        *,
        mode: TradingMode = "dry_run",
        max_rows: int = 10_000,
    ) -> list[dict[str, Any]]:
        """Load fills for round-trip pairing (oldest→newest batches via DESC pages)."""
        if not self.available:
            return []
        cap = max(1, min(int(max_rows), 50_000))
        out: list[dict[str, Any]] = []
        batch = 500
        offset = 0
        while offset < cap:
            page = self.list_fills(mode=mode, limit=batch, offset=offset)
            if not page:
                break
            out.extend(page)
            if len(page) < batch:
                break
            offset += batch
        return out[:cap]

    def summary(
        self,
        *,
        mode: TradingMode = "dry_run",
        fills_limit: int = 50,
        fills_offset: int = 0,
        marks: dict[str, float] | None = None,
        mark_sources: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Portfolio snapshot with **closed round-trip** rows as primary ``fills``.

        ``fills`` / ``fills_total`` are Backtest-style paired trades (entry+exit).
        Still-open legs appear only under ``positions``. Raw leg count is
        ``raw_fills_total``. Pairing uses ``pair_round_trip_trades`` (shared with
        the daily summary email).

        Pass ``marks`` (symbol → live LTP / last-known) to compute unrealized PnL.
        Optional ``mark_sources`` labels each mark (``ltp`` / ``last_known``).
        Without marks, uPnL stays 0 and ``mark_source`` is ``none``.
        """
        positions_raw = self.list_positions(mode=mode)
        raw_fills_total = self.count_fills(mode=mode)
        all_fills = self.list_all_fills(mode=mode)
        closed = sort_closed_trades_newest_first(pair_round_trip_trades(all_fills))
        lim = max(1, min(int(fills_limit), 500))
        off = max(0, int(fills_offset))
        page = closed[off : off + lim]
        day_realized = self.day_realized_pnl(mode=mode)
        mark_map = {
            str(sym).upper(): float(px)
            for sym, px in (marks or {}).items()
            if px is not None and float(px) > 0
        }
        source_map = {
            str(sym).upper(): str(src)
            for sym, src in (mark_sources or {}).items()
            if src
        }
        positions: list[dict[str, Any]] = []
        day_unrealized = 0.0
        for p in positions_raw:
            sym = str(p["symbol"]).upper()
            avg = float(p["avg_price"])
            qty = int(p["qty"])
            side = str(p["side"])
            mark_px = mark_map.get(sym)
            if mark_px is not None:
                upnl = unrealized_equity_pnl(side, avg, mark_px, qty)
                mark_source = source_map.get(sym, "ltp")
            else:
                upnl = 0.0
                mark_source = "none"
            day_unrealized += upnl
            entry = _position_entry_fields(p.get("opened_at"))
            positions.append(
                {
                    "symbol": p["symbol"],
                    "side": side,
                    "qty": qty,
                    "avg_price": avg,
                    "mark_price": mark_px,
                    "mark_source": mark_source,
                    "unrealized_pnl": upnl,
                    **entry,
                }
            )
        return {
            "mode": mode,
            "instrument": "equity",
            "execution": "dry_run",
            "dry_run": True,
            "source": "paper_ledger",
            "positions": positions,
            "fills": page,
            "fills_kind": "round_trip",
            "fills_total": len(closed),
            "fills_offset": off,
            "fills_limit": lim,
            "closed_trades": page,
            "closed_trades_total": len(closed),
            "raw_fills_total": raw_fills_total,
            "day_realized": day_realized,
            "day_unrealized": day_unrealized,
            "fees_est": 0.0,
            "open_symbols": [p["symbol"] for p in positions],
            "retention": {
                "auto_purge": False,
                "history_scope": "all_fills",
                "note": (
                    "Dry-run fills are retained until an explicit purge/archive "
                    "before live. Trades UI shows closed round-trips (paginated; "
                    "not today-only). Open legs stay under Open positions. "
                    "uPnL uses live LTP marks when market data is available; "
                    "tick gaps keep last-known live marks instead of zeroing."
                ),
            },
        }

    def preview_closed_purge(self, *, mode: TradingMode = "dry_run") -> dict[str, Any]:
        """Counts for a closed-only purge (no writes)."""
        if not self.available:
            return {
                "available": False,
                "mode": mode,
                "closed_trades": 0,
                "fills_to_delete": 0,
                "fills_kept": 0,
                "open_positions": 0,
            }
        all_fills = self.list_all_fills(mode=mode)
        closed = pair_round_trip_trades(all_fills)
        delete_ids = closed_round_trip_fill_ids(all_fills)
        positions = self.list_positions(mode=mode)
        return {
            "available": True,
            "mode": mode,
            "closed_trades": len(closed),
            "fills_to_delete": len(delete_ids),
            "fills_kept": max(0, len(all_fills) - len(delete_ids)),
            "raw_fills_total": len(all_fills),
            "open_positions": len(positions),
            "open_symbols": [str(p["symbol"]) for p in positions],
        }

    def purge_closed_trades(
        self,
        *,
        mode: TradingMode = "dry_run",
        confirm: bool = False,
        archive: bool = True,
    ) -> dict[str, Any]:
        """Delete completed round-trip fill legs only; keep open positions + open entries.

        Uses the same pairing as Trades UI / daily summary. Never deletes
        ``paper_positions`` rows. Requires ``confirm=True``.
        """
        if not confirm:
            return {
                "purged": False,
                "reason": "confirm=false — refusing to purge closed trades",
                "closed_only": True,
                "fills_deleted": 0,
                "positions_deleted": 0,
                "closed_trades_purged": 0,
                "archived": 0,
                "open_positions_kept": 0,
            }
        if not self.available:
            return {
                "purged": False,
                "reason": "store unavailable",
                "closed_only": True,
                "fills_deleted": 0,
                "positions_deleted": 0,
                "closed_trades_purged": 0,
                "archived": 0,
                "open_positions_kept": 0,
            }

        preview = self.preview_closed_purge(mode=mode)
        all_fills = self.list_all_fills(mode=mode)
        closed = pair_round_trip_trades(all_fills)
        delete_ids = sorted(closed_round_trip_fill_ids(all_fills))
        positions_before = self.list_positions(mode=mode)

        if not delete_ids:
            return {
                "purged": True,
                "mode": mode,
                "closed_only": True,
                "archived": 0,
                "fills_deleted": 0,
                "positions_deleted": 0,
                "closed_trades_purged": 0,
                "open_positions_kept": len(positions_before),
                "open_symbols_kept": [str(p["symbol"]) for p in positions_before],
                "fills_kept": preview["fills_kept"],
                "reason": "no closed round-trips to purge",
            }

        archived_at = _utc_now().isoformat()
        archived = 0
        fills_deleted = 0
        with self._connect() as conn:
            apply_paper_ledger_schema(conn)
            # Chunk IN lists for SQLite parameter limits.
            chunk = 400
            for i in range(0, len(delete_ids), chunk):
                batch = delete_ids[i : i + chunk]
                placeholders = ",".join("?" * len(batch))
                if archive:
                    cur = conn.execute(
                        f"""
                        INSERT OR REPLACE INTO paper_fills_archive (
                          {_FILL_ROW_COLS}, archived_at
                        )
                        SELECT {_FILL_ROW_COLS}, ? FROM paper_fills
                        WHERE mode = ? AND id IN ({placeholders})
                        """,
                        (archived_at, mode, *batch),
                    )
                    archived += int(cur.rowcount or 0)
                cur = conn.execute(
                    f"DELETE FROM paper_fills WHERE mode = ? AND id IN ({placeholders})",
                    (mode, *batch),
                )
                fills_deleted += int(cur.rowcount or 0)
            conn.commit()

        positions_after = self.list_positions(mode=mode)
        logger.warning(
            "paper ledger closed-only purge mode=%s archived=%d fills_deleted=%d "
            "closed_trades=%d open_positions_kept=%d",
            mode,
            archived,
            fills_deleted,
            len(closed),
            len(positions_after),
        )
        return {
            "purged": True,
            "mode": mode,
            "closed_only": True,
            "archived": archived if archive else 0,
            "fills_deleted": fills_deleted,
            "positions_deleted": 0,
            "closed_trades_purged": len(closed),
            "open_positions_kept": len(positions_after),
            "open_symbols_kept": [str(p["symbol"]) for p in positions_after],
            "fills_kept": self.count_fills(mode=mode),
            "archived_at": archived_at if archive else None,
        }

    def archive_and_purge(
        self,
        *,
        mode: TradingMode = "dry_run",
        confirm: bool = False,
        archive: bool = True,
        clear_positions: bool = True,
        closed_only: bool = False,
    ) -> dict[str, Any]:
        """Explicit purge path before starting live — never auto-called in dry_run.

        Copies fills into ``paper_fills_archive`` (when ``archive=True``), then
        deletes live ``paper_fills`` (and optionally ``paper_positions``) for
        ``mode``. With ``closed_only=True``, only completed round-trip fill legs
        are removed and open positions are always kept. Requires ``confirm=True``.
        """
        if closed_only:
            return self.purge_closed_trades(
                mode=mode, confirm=confirm, archive=archive
            )
        if not confirm:
            return {
                "purged": False,
                "reason": "confirm=false — refusing to purge paper ledger",
                "closed_only": False,
                "fills_deleted": 0,
                "positions_deleted": 0,
                "archived": 0,
            }
        if not self.available:
            return {
                "purged": False,
                "reason": "store unavailable",
                "closed_only": False,
                "fills_deleted": 0,
                "positions_deleted": 0,
                "archived": 0,
            }

        archived_at = _utc_now().isoformat()
        with self._connect() as conn:
            apply_paper_ledger_schema(conn)
            archived = 0
            if archive:
                cur = conn.execute(
                    f"""
                    INSERT OR REPLACE INTO paper_fills_archive (
                      {_FILL_ROW_COLS}, archived_at
                    )
                    SELECT {_FILL_ROW_COLS}, ? FROM paper_fills WHERE mode = ?
                    """,
                    (archived_at, mode),
                )
                archived = int(cur.rowcount or 0)
            fills_cur = conn.execute(
                "DELETE FROM paper_fills WHERE mode = ?",
                (mode,),
            )
            fills_deleted = int(fills_cur.rowcount or 0)
            positions_deleted = 0
            if clear_positions:
                pos_cur = conn.execute(
                    "DELETE FROM paper_positions WHERE mode = ?",
                    (mode,),
                )
                positions_deleted = int(pos_cur.rowcount or 0)
            conn.commit()

        logger.warning(
            "paper ledger purge mode=%s archived=%d fills_deleted=%d positions_deleted=%d",
            mode,
            archived,
            fills_deleted,
            positions_deleted,
        )
        return {
            "purged": True,
            "mode": mode,
            "closed_only": False,
            "archived": archived if archive else 0,
            "fills_deleted": fills_deleted,
            "positions_deleted": positions_deleted,
            "archived_at": archived_at if archive else None,
        }

    def run_live_retention(
        self,
        *,
        mode: TradingMode,
        min_retain_days: int = 90,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """Drop fills older than ``min_retain_days`` — **live mode only**.

        During dry_run/demo this is a no-op so review-week history is kept.
        After live unlock, prefer ``min_retain_days >= 90``.
        """
        if mode in {"dry_run", "demo"}:
            return {
                "purged": 0,
                "skipped": True,
                "reason": f"mode={mode} — auto-delete disabled; keep all paper fills",
            }
        if not self.available:
            return {"purged": 0, "skipped": True, "reason": "store unavailable"}

        days = max(90, int(min_retain_days))  # never retain less than 90 days
        now = as_of or _utc_now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        cutoff = (now - timedelta(days=days)).astimezone(timezone.utc).isoformat()

        with self._connect() as conn:
            apply_paper_ledger_schema(conn)
            cur = conn.execute(
                """
                DELETE FROM paper_fills
                WHERE mode = ? AND ts_utc < ?
                """,
                (mode, cutoff),
            )
            purged = int(cur.rowcount or 0)
            conn.commit()

        if purged:
            logger.info(
                "live paper fill retention purged=%d mode=%s older_than_days=%d",
                purged,
                mode,
                days,
            )
        return {
            "purged": purged,
            "skipped": False,
            "mode": mode,
            "min_retain_days": days,
            "cutoff_utc": cutoff,
        }
