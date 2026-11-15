"""Equity dry-run order intents (share buy/sell — not options CE/PE)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from options_portal_domain.types import SignalAction

EquitySide = Literal["BUY", "SELL"]
PositionSide = Literal["long", "short"]
EquityActionKind = Literal["entry", "exit", "flip_close", "flip_open"]


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


def plan_equity_intents(
    symbol: str,
    action: SignalAction | str,
    *,
    open_side: PositionSide | None,
    open_qty: int,
    qty: int,
    price: float,
    reason: str = "",
) -> list[EquityOrderIntent]:
    """Map hybrid bias → equity share intents.

    - BUY_CE_BIAS → long (BUY entry); exit short first if flipping
    - BUY_PE_BIAS → short (SELL entry); exit long first if flipping
    - HOLD / EXIT / REDUCE → close open paper position when present
    """
    sym = symbol.upper()
    act = action.value if isinstance(action, SignalAction) else str(action)
    px = float(price)
    share_qty = max(1, int(qty))
    pos_qty = max(0, int(open_qty))
    reason_txt = reason or act
    intents: list[EquityOrderIntent] = []

    def _exit_intent(kind: EquityActionKind = "exit") -> EquityOrderIntent | None:
        if open_side is None or pos_qty <= 0:
            return None
        if open_side == "long":
            return EquityOrderIntent(
                symbol=sym,
                side="SELL",
                qty=pos_qty,
                limit_price=px,
                reason=f"{reason_txt} · close long",
                action_kind=kind,
                resulting_position=None,
                signal_action=act,
            )
        return EquityOrderIntent(
            symbol=sym,
            side="BUY",
            qty=pos_qty,
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
