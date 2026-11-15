"""Equity dry-run paper ledger (SQLite local profile)."""

from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from options_portal_domain.equity import EquityOrderIntent, PositionSide
from options_portal_domain.tz import ist
from options_portal_domain.types import OrderResult, TradingMode
from options_portal_persistence.db import _sqlite_path_from_url, database_url, is_local_profile
from options_portal_persistence.sqlite_schema import apply_paper_ledger_schema

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _format_ist(dt: datetime) -> tuple[str, str]:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(ist())
    return local.isoformat(), local.strftime("%Y-%m-%d %H:%M IST")


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
                    if pos["side"] == "long":
                        realized = (float(intent.limit_price) - avg) * qty
                    else:
                        realized = (avg - float(intent.limit_price)) * qty
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

    def list_fills(
        self,
        *,
        mode: TradingMode = "dry_run",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if not self.available:
            return []
        with self._connect() as conn:
            apply_paper_ledger_schema(conn)
            rows = conn.execute(
                """
                SELECT * FROM paper_fills
                WHERE mode = ?
                ORDER BY ts_utc DESC
                LIMIT ?
                """,
                (mode, max(1, int(limit))),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
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
            )
        return out

    def summary(self, *, mode: TradingMode = "dry_run") -> dict[str, Any]:
        positions_raw = self.list_positions(mode=mode)
        fills = self.list_fills(mode=mode, limit=200)
        day_realized = self.day_realized_pnl(mode=mode)
        positions = [
            {
                "symbol": p["symbol"],
                "side": p["side"],
                "qty": int(p["qty"]),
                "avg_price": float(p["avg_price"]),
                "unrealized_pnl": 0.0,  # mark-to-market deferred; spot not cached here
                "opened_at": p["opened_at"],
            }
            for p in positions_raw
        ]
        return {
            "mode": mode,
            "instrument": "equity",
            "execution": "dry_run",
            "dry_run": True,
            "source": "paper_ledger",
            "positions": positions,
            "fills": fills,
            "day_realized": day_realized,
            "day_unrealized": 0.0,
            "fees_est": 0.0,
            "open_symbols": [p["symbol"] for p in positions],
        }
