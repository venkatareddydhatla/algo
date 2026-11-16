"""Cycle-scoped equity entry admission — rank by |hybrid|, fill empty slots.

During a full-universe scan, new ``entry`` intents are staged instead of
first-fill by CSV/scan order. After the batch, strongest |hybrid| candidates
fill free ``max_open_symbols`` slots. Existing opens are kept (anti-churn).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from options_portal_domain.entry_selection import (
    EntryCandidate,
    entry_selection_config,
    select_entries_for_empty_slots,
)
from options_portal_domain.equity import EquityOrderIntent
from options_portal_notify.factory import build_gmail_notifier
from options_portal_risk.order_router import SafeOrderRouter

logger = logging.getLogger(__name__)


@dataclass
class PendingEntry:
    """Deferred new-entry intents for one symbol in a scan cycle."""

    symbol: str
    hybrid_score: float
    confidence: float
    intents: list[EquityOrderIntent]
    safe_mode: str
    limits: dict[str, Any]
    recommendation: str = ""


@dataclass
class EntryCycleAdmission:
    """Collect new entries during ``_run_batch``; admit by |hybrid| at flush."""

    max_open_symbols: int
    enabled: bool = True
    anti_churn: str = "empty_slots_only"
    _pending: dict[str, PendingEntry] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @classmethod
    def from_config(cls, cfg: dict[str, Any] | None) -> EntryCycleAdmission | None:
        sel = entry_selection_config(cfg)
        if not sel["enabled"]:
            return None
        return cls(
            max_open_symbols=int(sel["max_open_symbols"]),
            enabled=True,
            anti_churn=str(sel["anti_churn"]),
        )

    async def stage(self, pending: PendingEntry) -> None:
        """Keep the strongest |hybrid| pending row per symbol."""
        sym = pending.symbol.upper()
        async with self._lock:
            prev = self._pending.get(sym)
            if prev is None or abs(pending.hybrid_score) > abs(prev.hybrid_score):
                self._pending[sym] = PendingEntry(
                    symbol=sym,
                    hybrid_score=float(pending.hybrid_score),
                    confidence=float(pending.confidence),
                    intents=list(pending.intents),
                    safe_mode=pending.safe_mode,
                    limits=dict(pending.limits),
                    recommendation=pending.recommendation,
                )

    def pending_candidates(self) -> list[EntryCandidate]:
        return [
            EntryCandidate(
                symbol=p.symbol,
                hybrid_score=p.hybrid_score,
                confidence=p.confidence,
            )
            for p in self._pending.values()
        ]

    async def flush(self, cfg: dict[str, Any]) -> dict[str, Any]:
        """Admit top |hybrid| into free slots and execute paper fills."""
        async with self._lock:
            pending = dict(self._pending)
            self._pending.clear()

        if not pending:
            return {"admitted": [], "skipped": [], "open_before": [], "slots": 0}

        open_symbols: list[str] = []
        paper = None
        try:
            from options_portal_persistence.paper_trades import PaperTradeStore

            paper = PaperTradeStore()
            if paper.available:
                # Prefer dry_run ledger mode (same as pipeline)
                snap = paper.portfolio_snapshot(mode="dry_run")
                open_symbols = list(snap.get("open_symbols") or [])
        except Exception:  # noqa: BLE001
            logger.exception("entry admission: paper ledger unavailable at flush")

        selected = select_entries_for_empty_slots(
            [
                EntryCandidate(p.symbol, p.hybrid_score, p.confidence)
                for p in pending.values()
            ],
            open_symbols=open_symbols,
            max_open_symbols=self.max_open_symbols,
        )
        admitted_syms = {c.symbol.upper() for c in selected}
        skipped = sorted(s for s in pending if s not in admitted_syms)

        logger.info(
            "Entry selection anti_churn=%s max_open=%d open=%d candidates=%d "
            "admitted=%s skipped=%d",
            self.anti_churn,
            self.max_open_symbols,
            len(open_symbols),
            len(pending),
            [c.symbol for c in selected],
            len(skipped),
        )
        if skipped:
            logger.info(
                "Entry selection skipped (weaker |hybrid| or no free slots): %s",
                ", ".join(
                    f"{s}|h|={abs(pending[s].hybrid_score):.3f}" for s in skipped[:30]
                )
                + ("..." if len(skipped) > 30 else ""),
            )

        router = SafeOrderRouter(halt=bool((cfg.get("trading") or {}).get("halt")))
        gmail = build_gmail_notifier(cfg)
        executed: list[str] = []

        for cand in selected:
            row = pending[cand.symbol.upper()]
            ok = await _execute_pending_entry(
                row, paper=paper, router=router, gmail=gmail
            )
            if ok:
                executed.append(cand.symbol)

        return {
            "admitted": executed,
            "skipped": skipped,
            "open_before": open_symbols,
            "slots": max(0, self.max_open_symbols - len(open_symbols)),
            "anti_churn": self.anti_churn,
        }


async def _execute_pending_entry(
    pending: PendingEntry,
    *,
    paper: Any,
    router: SafeOrderRouter,
    gmail: Any,
) -> bool:
    """Run deferred entry intents through the same dry-run paper path."""
    safe_mode = pending.safe_mode
    limits = dict(pending.limits)
    any_ok = False
    for intent in pending.intents:
        portfolio: dict[str, Any] = {}
        if paper is not None and paper.available:
            portfolio = paper.portfolio_snapshot(mode=safe_mode)  # type: ignore[arg-type]

        order = await router.execute_equity(
            intent,
            safe_mode,  # type: ignore[arg-type]
            actor_user_id=None,
            portfolio=portfolio,
            limits=limits,
        )
        if (
            paper is not None
            and paper.available
            and order.status == "simulated"
        ):
            persist_meta = paper.record_simulated_fill(
                intent, order, mode=safe_mode  # type: ignore[arg-type]
            )
            any_ok = True
            if persist_meta.get("persisted") and gmail is not None and gmail.enabled:
                try:
                    gmail.send_fill(
                        {
                            "symbol": intent.symbol,
                            "side": intent.side,
                            "qty": intent.qty,
                            "price": intent.limit_price,
                            "reason": (
                                f"{intent.reason} · ranked_|hybrid|="
                                f"{abs(pending.hybrid_score):.3f}"
                            ),
                            "action_kind": intent.action_kind,
                            "ts_ist_display": persist_meta.get("ts_ist_display"),
                        },
                        mode=str(safe_mode),
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Gmail fill notify failed for deferred entry %s", intent.symbol
                    )
        elif order.status == "simulated":
            any_ok = True
        else:
            logger.info(
                "Deferred entry %s not filled: %s (%s)",
                intent.symbol,
                order.status,
                order.message,
            )
    if any_ok:
        logger.info(
            "Admitted entry %s |hybrid|=%.3f %s",
            pending.symbol,
            pending.hybrid_score,
            pending.recommendation or "",
        )
    return any_ok
