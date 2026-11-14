"""Single-leg ATM / nearest liquid options recommender (v1)."""

from __future__ import annotations

from datetime import date
from typing import Any

from options_portal_domain.types import (
    HybridDecision,
    OptionLeg,
    OptionsRecommendation,
    SignalAction,
)


class SingleLegATMRecommender:
    """Recommend one CE or PE at ATM±offset with OI/spread filters."""

    def __init__(
        self,
        *,
        atm_offset: int = 0,
        min_oi: int = 50000,
        max_spread_pct: float = 2.0,
        lot_sizes: dict[str, int] | None = None,
    ) -> None:
        self.atm_offset = atm_offset
        self.min_oi = min_oi
        self.max_spread_pct = max_spread_pct
        self.lot_sizes = lot_sizes or {
            "NIFTY": 25,
            "BANKNIFTY": 15,
            "RELIANCE": 250,
            "TCS": 175,
        }

    def recommend(
        self,
        decision: HybridDecision,
        chain: dict[str, Any],
        risk_profile: dict[str, Any],
    ) -> OptionsRecommendation | None:
        if decision.action not in {SignalAction.BUY_CE_BIAS, SignalAction.BUY_PE_BIAS}:
            return None

        option_type = "CE" if decision.action == SignalAction.BUY_CE_BIAS else "PE"
        spot = float(chain.get("spot") or 0)
        atm = float(chain.get("atm") or spot)
        expiry = date.fromisoformat(str(chain["expiry"]))
        strikes = chain.get("strikes") or []
        if not strikes:
            return None

        # Pick ATM ± offset steps along sorted strikes
        sorted_strikes = sorted(strikes, key=lambda s: float(s["strike"]))
        atm_idx = min(
            range(len(sorted_strikes)),
            key=lambda i: abs(float(sorted_strikes[i]["strike"]) - atm),
        )
        target_idx = max(0, min(len(sorted_strikes) - 1, atm_idx + self.atm_offset))
        row = sorted_strikes[target_idx]
        side = row.get(option_type.lower()) or row.get(option_type)
        if not side:
            return None

        oi = int(side.get("oi") or 0)
        if oi < self.min_oi:
            # Nearest liquid: walk outward for higher OI
            found = None
            for offset in range(0, len(sorted_strikes)):
                for idx in {target_idx - offset, target_idx + offset}:
                    if idx < 0 or idx >= len(sorted_strikes):
                        continue
                    cand = sorted_strikes[idx]
                    cside = cand.get(option_type.lower()) or cand.get(option_type)
                    if cside and int(cside.get("oi") or 0) >= self.min_oi:
                        found = (cand, cside)
                        break
                if found:
                    break
            if not found:
                return None
            row, side = found

        max_lots = int(risk_profile.get("max_lots_per_symbol", 2))
        lots = max(1, min(max_lots, 1))
        strike = float(row["strike"])
        ltp = float(side.get("ltp") or 0)
        leg = OptionLeg(
            tradingsymbol=str(side.get("tradingsymbol") or f"{decision.symbol}-{strike}-{option_type}"),
            option_type=option_type,  # type: ignore[arg-type]
            strike=strike,
            expiry=expiry,
            qty_lots=lots,
            limit_price=ltp if ltp else None,
            instrument_key=str(side.get("instrument_key") or ""),
        )
        lot_size = self.lot_sizes.get(decision.symbol.upper(), 1)
        rationale = (
            f"BUY {decision.symbol} {expiry.isoformat()} {int(strike)} {option_type} "
            f"× {lots} lot (lot_size={lot_size}) ATM±{self.atm_offset}; OI={oi}"
        )
        return OptionsRecommendation(
            symbol=decision.symbol,
            legs=[leg],
            expiry=expiry,
            spot_ref=spot,
            rationale=rationale,
            chain_meta={"atm": atm, "oi": oi, "source": chain.get("source")},
        )
