"""Equity dry-run order intents (share buy/sell — not options CE/PE).

Fill / mark prices
------------------
Paper fills (entry, exit, soft_flatten, cover) should use a **live market mark**
when market data is available:

1. **Preferred:** Upstox (or configured MD) last traded price / LTP for the symbol.
2. **Fallback:** last candle close — logged as a warning (historical bars can be
   stale vs live LTP; exit may incorrectly match entry).
3. **Last resort:** open position entry avg — logged loudly (realized PnL will be
   0; investigate MD rather than silently copying entry).

When today's session high/low is known (15m bars and/or LTP feed OHLC), reject
LTP / candle-close candidates outside ``[session_low, session_high]``. Never use
a stale candle close that prints above the session high (e.g. HINDZINC 529 vs
day high ~520). Session H/L is a **sanity reject only** — never substitute
session high or low as the exit / uPnL mark itself.

Also reject candidates that jump more than ``~3%`` vs the symbol's last good
live LTP (or entry when no recent LTP) so a day-low / stale print cannot fire
``pct_profit_2`` on a short (APOLLOHOSP 8738 vs LTP ~9230).

Risk exits (profit_candle, optional ±2%/ATR/morning) must use **live equity LTP** only;
missing / rejected marks skip the exit rather than falling through to candle
low or entry.

Never silently reuse entry price when LTP succeeded.
Never apply another symbol's LTP / candle close / entry avg to this symbol's fill —
marks are always resolved per instrument key / tradingsymbol.

Price floor
-----------
Symbols with LTP (or last close) below ``trading.risk.min_equity_price`` (default
₹40) are excluded from watchlist scoring and new entries. Open-book exits still
run when a position is already open.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal, Mapping, Sequence

from options_portal_domain.candles_agg import session_daily_high_low
from options_portal_domain.types import Candle, SignalAction

EquitySide = Literal["BUY", "SELL"]
PositionSide = Literal["long", "short"]
EquityActionKind = Literal["entry", "exit", "flip_close", "flip_open"]
EquityMarkSource = Literal["ltp", "candle_close", "entry_avg", "fallback"]

# Skip scoring / new entries when LTP or last close is below this (INR).
DEFAULT_MIN_EQUITY_PRICE = 40.0

# Refuse risk-exit marks that jump more than this vs last good LTP / entry.
# ±2% profit/stop clears the gate; a ~5.6% day-low false mark (APOLLOHOSP) does not.
DEFAULT_MARK_JUMP_PCT = 3.0

# Price → share qty for every equity dry-run entry (curated + custom, same path).
# >2000 → 100; [1000, 2000] → 200; [500, 1000) → 500; else → 2000.
DEFAULT_PRICE_QTY_GT_2000 = 100
DEFAULT_PRICE_QTY_1000_2000 = 200
DEFAULT_PRICE_QTY_500_1000 = 500
DEFAULT_PRICE_QTY_ELSE = 2000
DEFAULT_PRICE_QTY_FALLBACK = 1  # missing/invalid mark when tiers on
DEFAULT_MAX_EQUITY_QTY_PER_SYMBOL = DEFAULT_PRICE_QTY_ELSE

# Process-local last accepted live LTP per symbol (worker + tests).
_LAST_GOOD_LTP: dict[str, float] = {}


def clear_last_good_ltp_cache() -> None:
    """Test helper — wipe retained last-good LTP marks."""
    _LAST_GOOD_LTP.clear()


def remember_last_good_ltp(symbol: str, price: float) -> None:
    """Retain a positive live LTP for later consistency checks."""
    sym = str(symbol or "").strip().upper()
    if not sym:
        return
    try:
        px = float(price)
    except (TypeError, ValueError):
        return
    if px > 0:
        _LAST_GOOD_LTP[sym] = px


def last_good_ltp(symbol: str) -> float | None:
    """Return previously retained live LTP for ``symbol``, if any."""
    sym = str(symbol or "").strip().upper()
    if not sym:
        return None
    px = _LAST_GOOD_LTP.get(sym)
    if px is None or px <= 0:
        return None
    return float(px)


def mark_jump_pct(reference: float, candidate: float) -> float:
    """Absolute percent jump of ``candidate`` vs ``reference`` (0 if invalid)."""
    try:
        ref = float(reference)
        px = float(candidate)
    except (TypeError, ValueError):
        return 0.0
    if ref <= 0 or px <= 0:
        return 0.0
    return abs(px - ref) / ref * 100.0


def mark_consistent_with_recent(
    candidate: float,
    reference: float | None,
    *,
    max_jump_pct: float = DEFAULT_MARK_JUMP_PCT,
) -> bool:
    """True when ``candidate`` is missing-ref (no guard) or within ``max_jump_pct``.

    A missing / non-positive reference → True (bootstrap: accept first live LTP).
    """
    if reference is None:
        return True
    try:
        ref = float(reference)
        px = float(candidate)
        cap = float(max_jump_pct)
    except (TypeError, ValueError):
        return True
    if ref <= 0 or px <= 0:
        return False
    if cap <= 0:
        return True
    return mark_jump_pct(ref, px) <= cap + 1e-12


def risk_exit_mark_accepted(
    *,
    mark: float | None,
    mark_source: str | None,
    recent_ltp: float | None = None,
    entry_avg: float | None = None,
    max_jump_pct: float = DEFAULT_MARK_JUMP_PCT,
) -> tuple[bool, str]:
    """Whether ``mark`` may fire open-book risk exits (profit_candle / optional ±2% / …).

    Requires live ``ltp`` source and consistency vs last good LTP (else entry).
    """
    src = str(mark_source or "").strip().lower()
    if src != "ltp":
        return (
            False,
            f"risk exits require live LTP mark (got source={src or 'none'})",
        )
    if mark is None:
        return False, "risk exits require live LTP mark (missing)"
    try:
        px = float(mark)
    except (TypeError, ValueError):
        return False, "risk exits require live LTP mark (invalid)"
    if px <= 0:
        return False, "risk exits require live LTP mark (non-positive)"

    reference: float | None = None
    ref_label = "recent_ltp"
    if recent_ltp is not None:
        try:
            recent = float(recent_ltp)
        except (TypeError, ValueError):
            recent = 0.0
        if recent > 0:
            reference = recent
    if reference is None and entry_avg is not None:
        try:
            entry = float(entry_avg)
        except (TypeError, ValueError):
            entry = 0.0
        if entry > 0:
            reference = entry
            ref_label = "entry_avg"

    if reference is not None and not mark_consistent_with_recent(
        px, reference, max_jump_pct=max_jump_pct
    ):
        jump = mark_jump_pct(reference, px)
        return (
            False,
            (
                f"risk exit mark inconsistent vs {ref_label}: "
                f"mark={px:.4f} ref={reference:.4f} jump={jump:.3f}% "
                f"> {float(max_jump_pct):g}%"
            ),
        )
    return True, "ok"


@dataclass(frozen=True)
class EquityOrderIntent:
    """Paper equity order derived from hybrid CE/PE bias signals."""

    symbol: str
    side: EquitySide
    qty: int
    limit_price: float
    reason: str
    action_kind: EquityActionKind
    # Desired open side after this fill (None = flat / closed)
    resulting_position: PositionSide | None
    signal_action: str


@dataclass(frozen=True)
class EquityMark:
    """Resolved fill / mark-to-market price with provenance."""

    price: float
    source: EquityMarkSource
    detail: str = ""


def min_equity_price_from_config(config: Mapping[str, Any] | None) -> float:
    """``trading.risk.min_equity_price`` (default ₹40). ``0`` disables the floor."""
    root = dict(config or {})
    trading = dict(root.get("trading") or {})
    risk = dict(trading.get("risk") or {})
    equity = dict(trading.get("equity") or {})
    raw = risk.get("min_equity_price", equity.get("min_equity_price", DEFAULT_MIN_EQUITY_PRICE))
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return float(DEFAULT_MIN_EQUITY_PRICE)


def below_min_equity_price(
    price: float | None,
    *,
    min_price: float = DEFAULT_MIN_EQUITY_PRICE,
) -> bool:
    """True when a positive price is strictly below the configured floor."""
    floor = float(min_price)
    if floor <= 0 or price is None:
        return False
    try:
        px = float(price)
    except (TypeError, ValueError):
        return False
    return px > 0 and px < floor


def filter_rows_below_min_equity_price(
    rows: list[dict[str, Any]],
    *,
    min_price: float = DEFAULT_MIN_EQUITY_PRICE,
    price_keys: tuple[str, ...] = ("price", "ltp"),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split signal rows into (kept, dropped) by ``min_equity_price``.

    A row is dropped only when a positive mark on ``price_keys`` is strictly
    below the floor. Missing/unknown prices are kept (fail-open for display).
    """
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for row in rows:
        px: float | None = None
        for key in price_keys:
            raw = row.get(key)
            if raw is None:
                continue
            try:
                candidate = float(raw)
            except (TypeError, ValueError):
                continue
            if candidate > 0:
                px = candidate
                break
        if below_min_equity_price(px, min_price=min_price):
            dropped.append(row)
        else:
            kept.append(row)
    return kept, dropped


def mark_within_session_hl(
    price: float,
    session_high: float | None,
    session_low: float | None,
) -> bool:
    """True when ``price`` is inside ``[session_low, session_high]``.

    Missing / invalid bounds → True (no guard). Requires ``high >= low > 0``.
    """
    try:
        px = float(price)
    except (TypeError, ValueError):
        return False
    if px <= 0:
        return False
    if session_high is None or session_low is None:
        return True
    try:
        hi = float(session_high)
        lo = float(session_low)
    except (TypeError, ValueError):
        return True
    if hi <= 0 or lo <= 0 or hi < lo:
        return True
    return lo <= px <= hi


def session_mark_bounds(
    candles: Sequence[Candle] | None = None,
    *,
    session_day: date | None = None,
    now: datetime | None = None,
    feed_high: float | None = None,
    feed_low: float | None = None,
) -> tuple[float, float] | None:
    """Today's session high/low for exit-mark guards.

    Combines 15m (or any) bar extremes via ``session_daily_high_low`` with optional
    LTP-feed day OHLC high/low. Returns ``(high, low)`` or ``None``.
    """
    highs: list[float] = []
    lows: list[float] = []
    if candles:
        hl = session_daily_high_low(
            candles, session_day=session_day, now=now
        )
        if hl is not None:
            highs.append(float(hl[0]))
            lows.append(float(hl[1]))
    for raw in (feed_high,):
        if raw is None:
            continue
        try:
            px = float(raw)
        except (TypeError, ValueError):
            continue
        if px > 0:
            highs.append(px)
    for raw in (feed_low,):
        if raw is None:
            continue
        try:
            px = float(raw)
        except (TypeError, ValueError):
            continue
        if px > 0:
            lows.append(px)
    if not highs or not lows:
        return None
    hi = max(highs)
    lo = min(lows)
    if hi < lo:
        return None
    return hi, lo


def resolve_equity_mark(
    *,
    ltp: float | None = None,
    candle_close: float | None = None,
    entry_avg: float | None = None,
    min_price: float = 0.01,
    session_high: float | None = None,
    session_low: float | None = None,
    recent_ltp: float | None = None,
    max_jump_pct: float = DEFAULT_MARK_JUMP_PCT,
) -> EquityMark:
    """Pick an equity mark: LTP → candle close → entry avg → min fallback.

    When ``session_high`` / ``session_low`` are set, skip LTP or candle-close
    prints outside that band (stale/corrupt closes must not exceed the session
    high). Session extremes are never used as the mark itself.

    When ``recent_ltp`` (last good live LTP) is set, also skip candidates that
    jump more than ``max_jump_pct`` vs that reference — blocks day-low / stale
    ticks that still sit inside the session band (APOLLOHOSP 8738 vs ~9230).

    Entry avg remains a last resort even if outside the band so paper
    exits can still complete (manual close). Risk exits must require ``ltp``.

    Callers must log when ``source != "ltp"`` so stale/entry-copy is never silent
    while market data is expected to be healthy.
    """
    rejected: list[str] = []

    def _reject_jump(label: str, px: float) -> bool:
        if recent_ltp is None:
            return False
        try:
            recent = float(recent_ltp)
        except (TypeError, ValueError):
            return False
        if recent <= 0:
            return False
        if mark_consistent_with_recent(px, recent, max_jump_pct=max_jump_pct):
            return False
        jump = mark_jump_pct(recent, px)
        rejected.append(
            f"{label}={px:.4f} jump={jump:.3f}% vs recent_ltp={recent:.4f} "
            f"(max {float(max_jump_pct):g}%)"
        )
        return True

    if ltp is not None and float(ltp) > 0:
        px = float(ltp)
        if not mark_within_session_hl(px, session_high, session_low):
            rejected.append(
                f"ltp={px:.4f} outside session H/L "
                f"[{float(session_low):.4f}, {float(session_high):.4f}]"
            )
        elif _reject_jump("ltp", px):
            pass
        else:
            return EquityMark(
                price=px,
                source="ltp",
                detail="live market last/LTP",
            )

    if candle_close is not None and float(candle_close) > 0:
        px = float(candle_close)
        if not mark_within_session_hl(px, session_high, session_low):
            rejected.append(
                f"candle_close={px:.4f} outside session H/L "
                f"[{float(session_low):.4f}, {float(session_high):.4f}]"
            )
        elif _reject_jump("candle_close", px):
            pass
        else:
            detail = (
                "LTP unavailable — using last candle close "
                "(may match entry if historical bars are stale)"
            )
            if rejected:
                detail = (
                    "LTP rejected — using last candle close; "
                    + "; ".join(rejected)
                )
            return EquityMark(
                price=px,
                source="candle_close",
                detail=detail,
            )

    if entry_avg is not None and float(entry_avg) > 0:
        detail = (
            "LTP and candle close unavailable — reusing entry avg "
            "(PnL will be 0; investigate market data)"
        )
        if rejected:
            detail = (
                "LTP/candle rejected — reusing entry avg "
                f"({'; '.join(rejected)})"
            )
        return EquityMark(
            price=float(entry_avg),
            source="entry_avg",
            detail=detail,
        )

    detail = "no mark available — using minimum fallback price"
    if rejected:
        detail = f"no in-range mark — fallback ({'; '.join(rejected)})"
    return EquityMark(
        price=float(min_price),
        source="fallback",
        detail=detail,
    )


def realized_equity_pnl(
    position_side: PositionSide | str,
    entry_avg: float,
    exit_mark: float,
    qty: int,
) -> float:
    """Realized PnL: long (exit−entry)×qty; short (entry−exit)×qty."""
    q = max(0, int(qty))
    entry = float(entry_avg)
    exit_px = float(exit_mark)
    side = str(position_side).strip().lower()
    if side == "long":
        return (exit_px - entry) * q
    if side == "short":
        return (entry - exit_px) * q
    raise ValueError(f"unknown position_side={position_side!r}")


def unrealized_equity_pnl(
    position_side: PositionSide | str,
    entry_avg: float,
    mark: float,
    qty: int,
) -> float:
    """Mark-to-market uPnL (same geometry as realized vs a live mark)."""
    return realized_equity_pnl(position_side, entry_avg, mark, qty)


def unrealized_pnl_pct(
    position_side: PositionSide | str,
    entry_avg: float,
    mark: float,
) -> float:
    """Unrealized PnL as percent of entry price (qty-independent).

    Long: ``(mark - entry) / entry * 100``; short: ``(entry - mark) / entry * 100``.
    """
    entry = float(entry_avg)
    if entry <= 0:
        return 0.0
    side = str(position_side).strip().lower()
    m = float(mark)
    if side == "long":
        return (m - entry) / entry * 100.0
    if side == "short":
        return (entry - m) / entry * 100.0
    raise ValueError(f"unknown position_side={position_side!r}")


def profit_book_exit_qty(open_qty: int, *, fraction: float = 1.0) -> int:
    """Shares to exit for profit booking.

    Default ``fraction=1.0`` closes the full book (qty is currently 1).
    Use ``fraction=0.5`` later for a 50% scale-out when ``open_qty > 1``.
    """
    q = max(0, int(open_qty))
    if q <= 0:
        return 0
    frac = float(fraction)
    if frac >= 1.0:
        return q
    if frac <= 0:
        return 0
    # Scale-out: at least 1 share, never more than open.
    return max(1, min(q, int(q * frac)))


@dataclass(frozen=True)
class PriceQtyTiersPolicy:
    """Price-banded share qty for open/add / flip_open (all equities).

    Config under ``trading.equity.price_qty_tiers`` (or ``trading.risk`` mirror).
    Applies to curated CSV and custom watchlist symbols alike — one worker path.
    """

    enabled: bool = True
    qty_gt_2000: int = DEFAULT_PRICE_QTY_GT_2000
    qty_1000_2000: int = DEFAULT_PRICE_QTY_1000_2000
    qty_500_1000: int = DEFAULT_PRICE_QTY_500_1000
    qty_else: int = DEFAULT_PRICE_QTY_ELSE
    fallback_qty: int = DEFAULT_PRICE_QTY_FALLBACK

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None) -> PriceQtyTiersPolicy:
        cfg = dict(config or {})
        trading = dict(cfg.get("trading") or {})
        equity = dict(trading.get("equity") or {})
        risk = dict(trading.get("risk") or {})
        raw = dict(
            equity.get("price_qty_tiers")
            or risk.get("price_qty_tiers")
            or trading.get("price_qty_tiers")
            or {}
        )
        fallback = equity.get("share_qty", risk.get("equity_share_qty", DEFAULT_PRICE_QTY_FALLBACK))
        try:
            fallback_i = max(1, int(fallback))
        except (TypeError, ValueError):
            fallback_i = DEFAULT_PRICE_QTY_FALLBACK
        return cls(
            enabled=bool(raw.get("enabled", True)),
            qty_gt_2000=max(1, int(raw.get("qty_gt_2000", DEFAULT_PRICE_QTY_GT_2000))),
            qty_1000_2000=max(
                1, int(raw.get("qty_1000_2000", DEFAULT_PRICE_QTY_1000_2000))
            ),
            qty_500_1000=max(1, int(raw.get("qty_500_1000", DEFAULT_PRICE_QTY_500_1000))),
            qty_else=max(1, int(raw.get("qty_else", DEFAULT_PRICE_QTY_ELSE))),
            fallback_qty=fallback_i,
        )


def equity_qty_for_price(
    price: float | None,
    *,
    policy: PriceQtyTiersPolicy | None = None,
    config: Mapping[str, Any] | None = None,
) -> int:
    """Share qty from mark price — same bands for curated and custom equities.

    - price > 2000 → 100
    - 1000 ≤ price ≤ 2000 → 200
    - 500 ≤ price < 1000 → 500
    - else (price < 500 or invalid) → 2000 when tiers on; fallback_qty if mark ≤ 0
    """
    pol = policy or PriceQtyTiersPolicy.from_config(config)
    if not pol.enabled:
        return max(1, int(pol.fallback_qty))
    try:
        px = float(price) if price is not None else 0.0
    except (TypeError, ValueError):
        px = 0.0
    if px <= 0:
        return max(1, int(pol.fallback_qty))
    if px > 2000.0:
        return max(1, int(pol.qty_gt_2000))
    if px >= 1000.0:
        return max(1, int(pol.qty_1000_2000))
    if px >= 500.0:
        return max(1, int(pol.qty_500_1000))
    return max(1, int(pol.qty_else))


def plan_equity_intents(
    symbol: str,
    action: SignalAction | str,
    *,
    open_side: PositionSide | None,
    open_qty: int,
    qty: int,
    price: float,
    reason: str = "",
    exit_qty: int | None = None,
) -> list[EquityOrderIntent]:
    """Map signal action → equity share intents.

    - BUY / BUY_CE_BIAS → long (BUY entry); exit short first if flipping
    - SELL / BUY_PE_BIAS → short (SELL entry); exit long first if flipping
    - HOLD / EXIT / REDUCE → close open paper position when present
      (Pine dry-run path must not call this with HOLD — Pine HOLD = no trade)

    ``exit_qty`` caps how many open shares to close (default = full ``open_qty``).
    Useful for morning profit-book full exit today and 50% scale-out later.
    """
    sym = symbol.upper()
    act = action.value if isinstance(action, SignalAction) else str(action)
    px = float(price)
    share_qty = max(1, int(qty))
    pos_qty = max(0, int(open_qty))
    close_qty = (
        pos_qty
        if exit_qty is None
        else max(0, min(pos_qty, int(exit_qty)))
    )
    reason_txt = reason or act
    intents: list[EquityOrderIntent] = []

    def _exit_intent(kind: EquityActionKind = "exit") -> EquityOrderIntent | None:
        if open_side is None or close_qty <= 0:
            return None
        if open_side == "long":
            return EquityOrderIntent(
                symbol=sym,
                side="SELL",
                qty=close_qty,
                limit_price=px,
                reason=f"{reason_txt} · close long",
                action_kind=kind,
                resulting_position=None,
                signal_action=act,
            )
        return EquityOrderIntent(
            symbol=sym,
            side="BUY",
            qty=close_qty,
            limit_price=px,
            reason=f"{reason_txt} · cover short",
            action_kind=kind,
            resulting_position=None,
            signal_action=act,
        )

    if act in {SignalAction.HOLD.value, SignalAction.EXIT.value, SignalAction.REDUCE.value}:
        exit_i = _exit_intent("exit")
        return [exit_i] if exit_i else []

    if act in {SignalAction.BUY.value, SignalAction.BUY_CE_BIAS.value}:
        desired: PositionSide = "long"
        entry_side: EquitySide = "BUY"
    elif act in {SignalAction.SELL.value, SignalAction.BUY_PE_BIAS.value}:
        desired = "short"
        entry_side = "SELL"
    else:
        return []

    if open_side == desired and pos_qty > 0:
        # Already on the desired side — no duplicate entry
        return []

    if open_side is not None and pos_qty > 0 and open_side != desired:
        close_i = _exit_intent("flip_close")
        if close_i:
            intents.append(close_i)

    intents.append(
        EquityOrderIntent(
            symbol=sym,
            side=entry_side,
            qty=share_qty,
            limit_price=px,
            reason=f"{reason_txt} · {'long' if desired == 'long' else 'short'} entry",
            action_kind="flip_open" if intents else "entry",
            resulting_position=desired,
            signal_action=act,
        )
    )
    return intents
