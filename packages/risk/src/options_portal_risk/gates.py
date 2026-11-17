"""Position / premium / kill-switch risk gates."""

from __future__ import annotations

from typing import Any

from options_portal_domain.equity import EquityOrderIntent
from options_portal_domain.types import OptionsRecommendation


class DefaultRiskGate:
    def validate(
        self,
        reco: OptionsRecommendation,
        portfolio: dict[str, Any],
        limits: dict[str, Any],
    ) -> tuple[bool, str]:
        if limits.get("halt") or portfolio.get("halt"):
            return False, "Trading halt active — order blocked"

        max_lots = int(limits.get("max_lots_per_symbol", 2))
        for leg in reco.legs:
            if leg.qty_lots > max_lots:
                return False, f"Lots {leg.qty_lots} exceed max_lots_per_symbol={max_lots}"

        open_symbols = set(portfolio.get("open_symbols") or [])
        max_open = int(limits.get("max_open_symbols", 10))
        if reco.symbol not in open_symbols and len(open_symbols) >= max_open:
            return False, f"Max open symbols ({max_open}) reached"

        day_loss = float(portfolio.get("day_realized_pnl", 0) or 0)
        max_day_loss = float(limits.get("max_day_loss_inr", 25000))
        if day_loss < 0 and abs(day_loss) >= max_day_loss:
            return False, f"Day loss ₹{abs(day_loss):.0f} hit max_day_loss_inr={max_day_loss}"

        min_oi = int(limits.get("min_option_oi", 50000))
        oi = int((reco.chain_meta or {}).get("oi") or 0)
        if oi and oi < min_oi:
            return False, f"Option OI {oi} below min_option_oi={min_oi}"

        return True, "ok"

    def validate_equity(
        self,
        intent: EquityOrderIntent,
        portfolio: dict[str, Any],
        limits: dict[str, Any],
    ) -> tuple[bool, str]:
        """Risk caps for equity dry-run share orders (no option OI check)."""
        if limits.get("halt") or portfolio.get("halt"):
            return False, "Trading halt active — order blocked"

        max_qty = int(limits.get("max_equity_qty_per_symbol", limits.get("equity_share_qty", 10)))
        # Allow exits even if qty exceeds entry cap (full close of open position)
        if intent.action_kind in {"entry", "flip_open"} and intent.qty > max_qty:
            return False, f"Qty {intent.qty} exceeds max_equity_qty_per_symbol={max_qty}"

        open_symbols = set(portfolio.get("open_symbols") or [])
        max_open = int(limits.get("max_open_symbols", 10))
        is_new_entry = intent.action_kind in {"entry", "flip_open"} and intent.symbol not in open_symbols
        if is_new_entry and len(open_symbols) >= max_open:
            return False, f"Max open symbols ({max_open}) reached"

        day_loss = float(portfolio.get("day_realized_pnl", 0) or 0)
        max_day_loss = float(limits.get("max_day_loss_inr", 25000))
        if day_loss < 0 and abs(day_loss) >= max_day_loss:
            return False, f"Day loss ₹{abs(day_loss):.0f} hit max_day_loss_inr={max_day_loss}"

        return True, "ok"
