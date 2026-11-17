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

Never silently reuse entry price when LTP succeeded.
Never apply another symbol's LTP / candle close / entry avg to this symbol's fill —
marks are always resolved per instrument key / tradingsymbol.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from options_portal_domain.types import SignalAction

EquitySide = Literal["BUY", "SELL"]
PositionSide = Literal["long", "short"]
EquityActionKind = Literal["entry", "exit", "flip_close", "flip_open"]
EquityMarkSource = Literal["ltp", "candle_close", "entry_avg", "fallback"]


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


def resolve_equity_mark(
    *,
    ltp: float | None = None,
    candle_close: float | None = None,
    entry_avg: float | None = None,
    min_price: float = 0.01,
) -> EquityMark:
    """Pick an equity mark: LTP → candle close → entry avg → min fallback.

    Callers must log when ``source != "ltp"`` so stale/entry-copy is never silent
    while market data is expected to be healthy.
    """
    if ltp is not None and float(ltp) > 0:
        return EquityMark(
            price=float(ltp),
            source="ltp",
            detail="live market last/LTP",
        )
    if candle_close is not None and float(candle_close) > 0:
        return EquityMark(
            price=float(candle_close),
            source="candle_close",
            detail=(
                "LTP unavailable — using last candle close "
                "(may match entry if historical bars are stale)"
            ),
        )
    if entry_avg is not None and float(entry_avg) > 0:
        return EquityMark(
            price=float(entry_avg),
            source="entry_avg",
            detail=(
                "LTP and candle close unavailable — reusing entry avg "
                "(PnL will be 0; investigate market data)"
            ),
        )
    return EquityMark(
        price=float(min_price),
        source="fallback",
        detail="no mark available — using minimum fallback price",
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
    """Map hybrid bias → equity share intents.

    - BUY_CE_BIAS → long (BUY entry); exit short first if flipping
    - BUY_PE_BIAS → short (SELL entry); exit long first if flipping
    - HOLD / EXIT / REDUCE → close open paper position when present

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

    if act == SignalAction.BUY_CE_BIAS.value:
        desired: PositionSide = "long"
        entry_side: EquitySide = "BUY"
    elif act == SignalAction.BUY_PE_BIAS.value:
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
