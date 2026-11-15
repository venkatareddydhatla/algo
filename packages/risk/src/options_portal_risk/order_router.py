"""Mode-aware order router — dry-run default; live blocked in v1."""

from __future__ import annotations

import os
import uuid
from typing import Any

from options_portal_broker.adapter import LiveTradingBlockedError, UpstoxAdapter
from options_portal_domain.equity import EquityOrderIntent
from options_portal_domain.types import OptionsRecommendation, OrderResult, TradingMode
from options_portal_risk.gates import DefaultRiskGate


class SafeOrderRouter:
    """Executes recommendations with hard live-placement gates."""

    def __init__(
        self,
        broker: UpstoxAdapter | None = None,
        risk_gate: DefaultRiskGate | None = None,
        *,
        halt: bool = False,
        v1_block_live: bool = True,
    ) -> None:
        self.broker = broker or UpstoxAdapter()
        self.risk_gate = risk_gate or DefaultRiskGate()
        self.halt = halt or os.environ.get("TRADING_HALT", "").lower() in {
            "1",
            "true",
            "yes",
        }
        # Locked default: v1 never places live orders through this router
        self.v1_block_live = v1_block_live

    def _reject_live(self, mode: TradingMode) -> OrderResult | None:
        if mode == "live" and self.v1_block_live:
            return OrderResult(
                client_order_id=f"blocked-{uuid.uuid4().hex[:10]}",
                status="rejected",
                mode=mode,
                broker_order_id=None,
                message=(
                    "Live placement disabled for v1. "
                    "Switch to dry_run/demo; live remains gated until Project unlock."
                ),
            )
        return None

    async def execute(
        self,
        reco: OptionsRecommendation,
        mode: TradingMode,
        actor_user_id: str | None,
        *,
        portfolio: dict[str, Any] | None = None,
        limits: dict[str, Any] | None = None,
    ) -> OrderResult:
        _ = actor_user_id
        portfolio = portfolio or {}
        limits = dict(limits or {})
        limits["halt"] = limits.get("halt") or self.halt

        ok, reason = self.risk_gate.validate(reco, portfolio, limits)
        if not ok:
            return OrderResult(
                client_order_id=f"blocked-{uuid.uuid4().hex[:10]}",
                status="rejected",
                mode=mode,
                broker_order_id=None,
                message=reason,
            )

        live_block = self._reject_live(mode)
        if live_block is not None:
            return live_block

        effective_mode: TradingMode = mode
        if not reco.legs:
            return OrderResult(
                client_order_id=f"empty-{uuid.uuid4().hex[:10]}",
                status="rejected",
                mode=effective_mode,
                broker_order_id=None,
                message="No legs in recommendation",
            )

        leg = reco.legs[0]
        order_payload: dict[str, Any] = {
            "mode": effective_mode,
            "client_order_id": f"{effective_mode}-{uuid.uuid4().hex[:12]}",
            "tradingsymbol": leg.tradingsymbol,
            "instrument_key": leg.instrument_key,
            "side": "BUY",
            "product": "NRML",
            "quantity_lots": leg.qty_lots,
            "limit_price": leg.limit_price,
            "instrument": "option",
        }
        try:
            return await self.broker.place_order(order_payload)
        except LiveTradingBlockedError as exc:
            return OrderResult(
                client_order_id=order_payload["client_order_id"],
                status="rejected",
                mode=effective_mode,
                broker_order_id=None,
                message=str(exc),
            )

    async def execute_equity(
        self,
        intent: EquityOrderIntent,
        mode: TradingMode,
        actor_user_id: str | None = None,
        *,
        portfolio: dict[str, Any] | None = None,
        limits: dict[str, Any] | None = None,
    ) -> OrderResult:
        """Simulate equity share order (BUY long / SELL short). Never live in v1."""
        _ = actor_user_id
        portfolio = portfolio or {}
        limits = dict(limits or {})
        limits["halt"] = limits.get("halt") or self.halt

        ok, reason = self.risk_gate.validate_equity(intent, portfolio, limits)
        if not ok:
            return OrderResult(
                client_order_id=f"blocked-{uuid.uuid4().hex[:10]}",
                status="rejected",
                mode=mode,
                broker_order_id=None,
                message=reason,
            )

        live_block = self._reject_live(mode)
        if live_block is not None:
            return live_block

        effective_mode: TradingMode = "dry_run" if mode == "live" else mode
        client_order_id = f"eq-{effective_mode}-{uuid.uuid4().hex[:12]}"
        order_payload: dict[str, Any] = {
            "mode": effective_mode,
            "client_order_id": client_order_id,
            "tradingsymbol": intent.symbol,
            "instrument_key": f"NSE_EQ|{intent.symbol}",
            "side": intent.side,
            "product": "CNC",
            "quantity": intent.qty,
            "quantity_lots": 1,
            "limit_price": intent.limit_price,
            "instrument": "equity",
            "reason": intent.reason,
            "action_kind": intent.action_kind,
        }
        try:
            result = await self.broker.place_order(order_payload)
            # Annotate message for equity paper path
            if result.status == "simulated" and not (result.message or "").startswith("Equity"):
                return OrderResult(
                    client_order_id=result.client_order_id,
                    status=result.status,
                    mode=result.mode,
                    broker_order_id=result.broker_order_id,
                    message=(
                        f"Equity dry_run paper fill: {intent.side} {intent.qty} "
                        f"{intent.symbol} @ {intent.limit_price:.2f} ({intent.action_kind})"
                    ),
                )
            return result
        except LiveTradingBlockedError as exc:
            return OrderResult(
                client_order_id=client_order_id,
                status="rejected",
                mode=effective_mode,
                broker_order_id=None,
                message=str(exc),
            )
