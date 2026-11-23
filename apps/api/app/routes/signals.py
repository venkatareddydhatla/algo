"""Signal read APIs (mock-backed until persistence wired)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.marks import enrich_rows_with_prices
from app.mock_data import (
    MOCK_SIGNALS,
    apply_open_trade_display_actions,
    build_universe_signals,
    clear_persisted_pine_signals,
    rows_for_top_strip,
)
from app.pine_refresh import (
    enforce_bearish_pivot_invariant,
    refresh_rows_from_candles,
)
from options_portal_domain.config import load_config, watchlist_symbols
from options_portal_domain.equity import (
    filter_rows_below_min_equity_price,
    min_equity_price_from_config,
)
from options_portal_domain.universe import INDEX_SYMBOLS, is_index_underlying
from options_portal_hybrid.explain import (
    pick_top_hybrid_sides,
    sort_signal_rows,
)

router = APIRouter(prefix="/api/v1", tags=["signals"])


# Watchlist / strip list payloads omit reason_* — UI shows Action + Trend only.
# Engine / paper / notify still keep full reasons; entries/exits unchanged.
_WATCHLIST_OMIT_REASON_KEYS = (
    "reasons",
    "reason_kind",
    "reason_summary",
    "reason_detail",
)


def _normalize_trend(trend: Any) -> str:
    t = str(trend or "NEUTRAL").strip().upper()
    if t in {"BULLISH", "BULL", "UP"}:
        return "BULLISH"
    if t in {"BEARISH", "BEAR", "DOWN"}:
        return "BEARISH"
    return "NEUTRAL"


def _omit_watchlist_reason_fields(row: dict[str, Any]) -> dict[str, Any]:
    """Return a copy without reason fields for Signals watchlist / strip rows."""
    out = dict(row)
    for key in _WATCHLIST_OMIT_REASON_KEYS:
        out.pop(key, None)
    return out


def _drop_index_action_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Indices may stay on MarketIndexStrip quotes — never as Action table rows."""
    kept: list[dict[str, Any]] = []
    for row in rows:
        sym = str(row.get("symbol") or "").upper()
        if not sym:
            continue
        if sym in INDEX_SYMBOLS or is_index_underlying(sym):
            continue
        kept.append(row)
    return kept


def _normalize_pine_actions(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        act = str(row.get("action") or "HOLD").upper()
        if act in {"BUY_CE_BIAS", "BUY_CE", "LONG"}:
            row["action"] = "BUY"
        elif act in {"BUY_PE_BIAS", "BUY_PE", "SHORT"}:
            row["action"] = "SELL"
        elif act not in {"BUY", "SELL", "HOLD"}:
            row["action"] = "HOLD"
        row["trend"] = _normalize_trend(row.get("trend"))
        # Keep pine_action aligned when present (before open-trade overlay).
        if "pine_action" in row:
            pine = str(row.get("pine_action") or "HOLD").upper()
            if pine in {"BUY_CE_BIAS", "BUY_CE", "LONG"}:
                row["pine_action"] = "BUY"
            elif pine in {"BUY_PE_BIAS", "BUY_PE", "SHORT"}:
                row["pine_action"] = "SELL"
            elif pine not in {"BUY", "SELL", "HOLD"}:
                row["pine_action"] = "HOLD"
        else:
            row["pine_action"] = row["action"]


async def _prepare_signal_rows(
    rows: list[dict[str, Any]],
    *,
    cfg: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Normalize → candle recompute (full watchlist) → price floor → overlay → invariant.

    Signals page reads ``action`` / ``trend`` from this path only — no alternate
    stale BUY channel. Always recomputes every equity row from live 15m candles
    on GET (worker idle / stale ``pine.signal.*`` cannot blanket the board).
    Hard gate: lastConfirmedPivotIsHigh → SELL / pivot low → BUY
    (independent of Trend).
    """
    config = cfg or load_config()
    _normalize_pine_actions(rows)
    # force_all: systemic fix — every curated + custom row follows last
    # confirmed Reversal Pro pivot + Trend from candles, not sticky KV.
    refresh_meta = await refresh_rows_from_candles(rows, cfg=config, force_all=True)
    _normalize_pine_actions(rows)
    rows, floor_meta = await _apply_min_equity_price_filter(rows, cfg=config)
    apply_open_trade_display_actions(rows)
    rows = _drop_index_action_rows(rows)
    forced = enforce_bearish_pivot_invariant(rows)
    meta = {
        **floor_meta,
        "pine_recomputed": refresh_meta.get("recomputed") or [],
        "pine_recompute_attempted": refresh_meta.get("attempted") or [],
        "pine_candle_symbols": int(refresh_meta.get("candle_symbols") or 0),
        "pine_candle_miss_cleared": refresh_meta.get("candle_miss_cleared") or [],
        "invariant_forced_sell": int(forced),
    }
    return rows, meta


async def _apply_min_equity_price_filter(
    rows: list[dict[str, Any]],
    *,
    cfg: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Enrich marks, drop LTP/close &lt; min_equity_price, clear stale Pine KV."""
    config = cfg or load_config()
    min_px = min_equity_price_from_config(config)
    mark_meta = await enrich_rows_with_prices(rows)
    kept, dropped = filter_rows_below_min_equity_price(rows, min_price=min_px)
    cleared: list[str] = []
    if dropped:
        cleared = clear_persisted_pine_signals(
            [str(r.get("symbol") or "") for r in dropped]
        )
    meta = {
        **mark_meta,
        "min_equity_price": min_px,
        "price_floor_dropped": sorted(
            {str(r.get("symbol") or "").upper() for r in dropped if r.get("symbol")}
        ),
        "pine_kv_cleared": cleared,
    }
    return kept, meta


def _load_signal_rows(
    *,
    symbol: str | None = None,
    expand_watchlist: bool = True,
) -> tuple[list[dict], str]:
    cfg = load_config()
    trading = cfg.get("trading", {})
    timeframe = str(trading.get("timeframe", "15m"))

    if expand_watchlist:
        watchlist = watchlist_symbols(cfg)
        rows = build_universe_signals(
            watchlist,
            timeframe=timeframe,
        )
        persisted = sum(1 for r in rows if r.get("pine_persisted"))
        source = (
            f"pine+kv({persisted})" if persisted else "pine+placeholder"
        )
    else:
        rows = [dict(s) for s in MOCK_SIGNALS]
        source = "mock"

    if symbol:
        needle = symbol.upper()
        rows = [s for s in rows if s["symbol"].upper() == needle]

    return rows, source


@router.get("/signals")
async def list_signals(
    symbol: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    expand_watchlist: bool = Query(
        True,
        description="Score the full watchlist (dry-run synthetic) instead of seed rows only",
    ),
) -> dict:
    """List signals sorted actionable-first, then by |score| desc.

    User-facing Action is Pine BUY/SELL/HOLD, except while a dry-run position is
    open: LONG → BUY, SHORT → SELL (display / mode only). ``pine_action`` keeps
    expire-stale signal freshness. Entries still require a fresh Pine bar.
    Symbols with LTP/close below ``min_equity_price`` (₹20) are omitted.
    """
    cfg = load_config()
    rows, source = _load_signal_rows(symbol=symbol, expand_watchlist=expand_watchlist)
    rows, prep_meta = await _prepare_signal_rows(rows, cfg=cfg)
    rows = sort_signal_rows(rows)
    total = len(rows)
    page = [_omit_watchlist_reason_fields(r) for r in rows[offset : offset + limit]]
    sticky = sum(1 for r in page if r.get("action_from_open_trade"))
    return {
        "items": page,
        "total": total,
        "offset": offset,
        "limit": limit,
        "source": source,
        "sort": "actionable,|score|desc,confidence,symbol",
        "marks_applied": prep_meta.get("marks_applied", []),
        "price_sources": prep_meta.get("price_sources", {}),
        "min_equity_price": prep_meta.get("min_equity_price"),
        "price_floor_dropped": prep_meta.get("price_floor_dropped", []),
        "pine_recomputed": prep_meta.get("pine_recomputed", []),
        "pine_candle_miss_cleared": prep_meta.get("pine_candle_miss_cleared", []),
        "invariant_forced_sell": prep_meta.get("invariant_forced_sell", 0),
        "open_trade_action_overlay": sticky,
        "note": (
            "Action = Reversal Pro U1→BUY / D1→SELL; hard invariant: "
            "lastConfirmedPivotIsHigh → SELL / pivot low → BUY (ignores "
            "signal sticky and Trend). GET force-recomputes from Upstox 15m "
            "candles; if candles missing, clears pine.signal KV and refuses "
            "stale/synthetic BUY (pivot-high gate still applies on features). "
            "Worker never persists Pine from synthetic candles. "
            "Open-trade overlay never hides a fresher opposite pine Action. "
            "Watchlist rows omit reason_* (UI shows Action + Trend only). "
            f"Symbols below ₹{float(prep_meta.get('min_equity_price') or 20):g} "
            "are excluded from the Signals table."
        ),
    }


@router.get("/signals/top-hybrid")
async def top_hybrid(
    limit: int = Query(5, ge=1, le=20),
    expand_watchlist: bool = Query(True),
) -> dict:
    """Top BUY and SELL Pine strip candidates (ce/pe keys kept for UI compat).

    Strip ranking uses ``pine_action`` (fresh signal), not sticky open-trade Action.
    """
    cfg = load_config()
    rows, source = _load_signal_rows(expand_watchlist=expand_watchlist)
    # Same prepare path as list_signals (candle recompute + invariant).
    rows, prep_meta = await _prepare_signal_rows(rows, cfg=cfg)
    sides = pick_top_hybrid_sides(rows_for_top_strip(rows), limit=limit)
    return {
        "ce": [_omit_watchlist_reason_fields(r) for r in sides["ce"]],
        "pe": [_omit_watchlist_reason_fields(r) for r in sides["pe"]],
        "limit": limit,
        "source": source,
        "min_equity_price": prep_meta.get("min_equity_price"),
        "price_floor_dropped": prep_meta.get("price_floor_dropped", []),
        "pine_recomputed": prep_meta.get("pine_recomputed", []),
        "note": (
            "Top Pine BUY / SELL from fresh pine_action only "
            "(sticky open-trade Action does not enter ranking). "
            "Signals UI no longer shows TOP BUY/SELL strip. "
            "Index underlyings excluded. Rows omit reason_*. "
            f"Excludes symbols below ₹{float(prep_meta.get('min_equity_price') or 20):g}."
        ),
    }


@router.get("/signals/{signal_id}")
async def get_signal(signal_id: str) -> dict:
    cfg = load_config()
    watchlist = watchlist_symbols(cfg)
    rows = build_universe_signals(
        watchlist,
        timeframe=str(cfg.get("trading", {}).get("timeframe", "15m")),
    )
    prepared, _meta = await _prepare_signal_rows([dict(s) for s in rows], cfg=cfg)
    for row in prepared:
        if row.get("id") == signal_id:
            return {
                **row,
                "recommendation": {
                    "rationale": "BUY single-leg ATM (mock)",
                    "legs": [],
                },
            }
    raise HTTPException(status_code=404, detail="signal not found")
