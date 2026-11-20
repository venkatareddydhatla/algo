"""Cycle-scoped equity entry admission — |hybrid| rank + high-conviction capacity.

During a full-universe scan, new ``entry`` intents are staged instead of
first-fill by CSV/scan order. After the batch, strongest |hybrid| candidates
fill free slots under a dynamic target (floor/ceiling + min |hybrid|).

Multi-factor filters (MTF/RVOL≥1.0×/ATR/proximity 0.1×ATR skip-until-10:30/
news fail-open) run in the pipeline before staging. This module ranks admits,
applies cooloff / WR-based max-open, and uses time-of-day |hybrid| floors from
``high_conviction`` (+ adaptive delta). Anti-churn: ``empty_slots_only``.
Never invents a min-trade quota.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from options_portal_domain.adaptive_hybrid import load_adaptive_delta
from options_portal_domain.calendar import pnl_ist_day
from options_portal_domain.entry_selection import (
    EntryCandidate,
    count_morning_entries_from_fills,
    effective_min_hybrid_abs,
    entry_selection_config,
    in_morning_entry_window,
    morning_entry_slots_remaining,
    select_entries_for_empty_slots,
    symbols_in_reentry_cooloff,
)
from options_portal_domain.equity import EquityOrderIntent
from options_portal_domain.high_conviction import (
    effective_max_open_symbols,
    high_conviction_config,
    last_trade_times_from_fills,
    rolling_equity_win_rate,
    session_min_hybrid_abs,
    skip_entry_log,
)
from options_portal_domain.session_gates import SoftEodPolicy, is_trading_session
from options_portal_domain.tz import now_ist
from options_portal_domain.universe import is_index_underlying
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
    dynamic_enabled: bool = False
    floor: int = 3
    ceiling: int = 12
    min_hybrid_abs: float = 0.72
    afternoon_after_ist: str | None = None
    afternoon_min_hybrid_abs: float | None = None
    morning_enabled: bool = True
    morning_until_ist: str = "10:00"
    morning_min_hybrid_abs: float = 0.75
    morning_max_new_entries: int = 3
    cooloff_enabled: bool = True
    cooloff_minutes: int = 180
    cooloff_same_session: bool = True
    _pending: dict[str, PendingEntry] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @classmethod
    def from_config(cls, cfg: dict[str, Any] | None) -> EntryCycleAdmission | None:
        sel = entry_selection_config(cfg)
        if not sel["enabled"]:
            return None
        dyn = dict(sel.get("dynamic_capacity") or {})
        cool = dict(sel.get("reentry_cooloff") or {})
        morn = dict(sel.get("morning_session") or {})
        hard = int(sel["max_open_symbols"])
        ceiling = int(dyn.get("ceiling", hard))
        aft_after = dyn.get("afternoon_after_ist")
        aft_min = dyn.get("afternoon_min_hybrid_abs")
        return cls(
            max_open_symbols=hard,
            enabled=True,
            anti_churn=str(sel["anti_churn"]),
            dynamic_enabled=bool(dyn.get("enabled", False)),
            floor=int(dyn.get("floor", 3)),
            ceiling=ceiling,
            min_hybrid_abs=float(dyn.get("min_hybrid_abs", 0.72)),
            afternoon_after_ist=str(aft_after) if aft_after else None,
            afternoon_min_hybrid_abs=(
                float(aft_min) if aft_min is not None else None
            ),
            morning_enabled=bool(morn.get("enabled", True)),
            morning_until_ist=str(morn.get("until_ist", "10:00")),
            morning_min_hybrid_abs=float(morn.get("min_hybrid_abs", 0.75)),
            morning_max_new_entries=int(morn.get("max_new_entries", 3)),
            cooloff_enabled=bool(cool.get("enabled", True)),
            cooloff_minutes=int(cool.get("minutes", 180)),
            cooloff_same_session=bool(cool.get("same_session_only", True)),
        )

    async def stage(self, pending: PendingEntry) -> None:
        """Keep the strongest |hybrid| pending row per symbol.

        Index underlyings are refused even if already queued elsewhere — they
        must never enter the equity paper book via deferred admission.
        """
        sym = pending.symbol.upper()
        if is_index_underlying(sym):
            logger.info(
                "Entry admission refused index underlying %s (not equity stock)",
                sym,
            )
            return
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

    def _effective_min_hybrid(
        self,
        ist_now: Any,
        *,
        open_ist: str = "09:15",
        cfg: dict[str, Any] | None = None,
        adaptive_delta: float = 0.0,
        rolling_wr: float | None = None,
    ) -> float:
        """Prefer high-conviction time floors (+ adaptive); fall back to dual-session."""
        hc = high_conviction_config(cfg)
        if hc.get("enabled", True):
            return session_min_hybrid_abs(
                ist_now,
                before_12=float(hc["min_hybrid_before_12"]),
                after_12=float(hc["min_hybrid_after_12"]),
                after_1420=float(hc["min_hybrid_after_1420"]),
                afternoon_after_ist=str(hc["afternoon_after_ist"]),
                late_after_ist=str(hc["late_after_ist"]),
                adaptive_delta=adaptive_delta,
                rolling_wr=rolling_wr,
                late_base_min=float(hc["late_base_min_hybrid"]),
                late_base_wr_threshold=float(hc["late_base_wr_threshold"]),
            )
        return effective_min_hybrid_abs(
            self.min_hybrid_abs,
            now=ist_now,
            morning_until_ist=(
                self.morning_until_ist if self.morning_enabled else None
            ),
            morning_min_hybrid_abs=(
                self.morning_min_hybrid_abs if self.morning_enabled else None
            ),
            open_ist=open_ist,
            afternoon_after_ist=self.afternoon_after_ist,
            afternoon_min_hybrid_abs=self.afternoon_min_hybrid_abs,
        )

    async def flush(self, cfg: dict[str, Any]) -> dict[str, Any]:
        """Admit top |hybrid| into free slots and execute paper fills.

        Re-checks ``is_trading_session`` at flush time so a long overnight scan
        cannot admit/persist entry fills (or send entry Gmail) after hours.
        """
        async with self._lock:
            pending = dict(self._pending)
            self._pending.clear()

        if not pending:
            return {
                "admitted": [],
                "skipped": [],
                "open_before": [],
                "slots": 0,
                "target_open": 0,
                "dynamic": self.dynamic_enabled,
            }

        # Refuse index underlyings still sitting in the deferred queue.
        index_skipped = sorted(s for s in pending if is_index_underlying(s))
        for sym in index_skipped:
            pending.pop(sym, None)
            logger.info(
                "Entry admission flush dropped deferred index %s (index_not_equity)",
                sym,
            )
        if not pending:
            return {
                "admitted": [],
                "skipped": index_skipped,
                "open_before": [],
                "slots": 0,
                "target_open": 0,
                "dynamic": self.dynamic_enabled,
                "reason": "index_not_equity — deferred index entries dropped",
            }

        soft_policy = SoftEodPolicy.from_config(cfg)
        ist_now = now_ist()
        if not is_trading_session(ist_now, policy=soft_policy):
            skipped = sorted(pending)
            reason = (
                f"outside_entry_session entries_open={soft_policy.entries_open_ist} "
                f"no_new_entries_after={soft_policy.no_new_entries_after} IST "
                f"(dropped {len(skipped)} deferred entries)"
            )
            logger.info("Entry selection flush skipped: %s", reason)
            return {
                "admitted": [],
                "skipped": skipped,
                "open_before": [],
                "slots": 0,
                "target_open": 0,
                "dynamic": self.dynamic_enabled,
                "reason": reason,
            }

        open_symbols: list[str] = []
        cooloff_blocked: set[str] = set()
        morning_entries_used = 0
        fills: list[dict[str, Any]] = []
        paper = None
        try:
            from options_portal_persistence.paper_trades import PaperTradeStore

            paper = PaperTradeStore()
            if paper.available:
                # Prefer dry_run ledger mode (same as pipeline)
                snap = paper.portfolio_snapshot(mode="dry_run")
                open_symbols = list(snap.get("open_symbols") or [])
                fills = list(paper.list_all_fills(mode="dry_run"))
                if self.cooloff_enabled and self.cooloff_minutes > 0:
                    # Cooloff after **any** trade (entry or exit) on the symbol.
                    last_trades = last_trade_times_from_fills(fills)
                    cooloff_blocked = symbols_in_reentry_cooloff(
                        last_trades,
                        now=ist_now,
                        cooloff_minutes=self.cooloff_minutes,
                        same_session_only=self.cooloff_same_session,
                        session_day=pnl_ist_day(ist_now),
                    )
                if self.morning_enabled:
                    morning_entries_used = count_morning_entries_from_fills(
                        fills,
                        now=ist_now,
                        open_ist=soft_policy.open_ist,
                        morning_until_ist=self.morning_until_ist,
                        session_day=pnl_ist_day(ist_now),
                    )
        except Exception:  # noqa: BLE001
            logger.exception("entry admission: paper ledger unavailable at flush")

        hc = high_conviction_config(cfg)
        adaptive_delta = 0.0
        try:
            adaptive_delta = load_adaptive_delta(cfg=cfg)
        except Exception:  # noqa: BLE001
            logger.exception("entry admission: adaptive delta load failed")

        wr, wr_wins, wr_n = rolling_equity_win_rate(
            fills, window_trips=int(hc.get("wr_window_trips", 20))
        )
        hard_cap = effective_max_open_symbols(
            wr,
            max_open=int(hc.get("max_open", self.max_open_symbols)),
            max_open_low_wr=int(hc.get("max_open_low_wr", 4)),
            wr_threshold=float(hc.get("wr_threshold", 0.50)),
            min_trips_for_wr=int(hc.get("wr_min_trips", 5)),
            trip_count=wr_n,
        )
        # Never exceed YAML / admission hard cap.
        hard_cap = min(hard_cap, max(0, int(self.max_open_symbols)))
        ceiling = min(int(self.ceiling), hard_cap) if hard_cap > 0 else int(self.ceiling)

        # Drop cooloff symbols from pending before selection (still listed skipped).
        cooloff_skipped = sorted(s for s in pending if s in cooloff_blocked)
        for sym in cooloff_skipped:
            pending.pop(sym, None)

        eff_min = self._effective_min_hybrid(
            ist_now,
            open_ist=soft_policy.open_ist,
            cfg=cfg,
            adaptive_delta=adaptive_delta,
            rolling_wr=wr,
        )
        admit_top_pct: float | None = None
        if hc.get("enabled", True) and hc.get("top_pct_enabled", True):
            admit_top_pct = float(hc.get("top_pct_of_scan", 0.10))
        in_morning = self.morning_enabled and in_morning_entry_window(
            ist_now,
            open_ist=soft_policy.open_ist,
            morning_until_ist=self.morning_until_ist,
        )
        morning_remaining: int | None = None
        if in_morning:
            morning_remaining = morning_entry_slots_remaining(
                morning_entries_used=morning_entries_used,
                max_new_entries=self.morning_max_new_entries,
            )

        if not pending:
            return {
                "admitted": [],
                "skipped": sorted(set(index_skipped) | set(cooloff_skipped)),
                "open_before": open_symbols,
                "slots": 0,
                "target_open": len(open_symbols),
                "dynamic": self.dynamic_enabled,
                "cooloff_blocked": cooloff_skipped,
                "min_hybrid_abs_effective": eff_min,
                "adaptive_delta": adaptive_delta,
                "rolling_wr": wr,
                "max_open_effective": hard_cap,
                "morning_session": in_morning,
                "morning_entries_used": morning_entries_used,
                "morning_remaining": morning_remaining,
                "skip_log": skip_entry_log().snapshot(),
                "reason": "reentry_cooloff — all deferred entries blocked",
            }

        if in_morning and morning_remaining == 0:
            skipped = sorted(
                set(index_skipped) | set(cooloff_skipped) | set(pending)
            )
            reason = (
                f"morning_entry_budget_exhausted max_new={self.morning_max_new_entries} "
                f"used={morning_entries_used} until={self.morning_until_ist} IST"
            )
            logger.info("Entry selection flush skipped: %s", reason)
            return {
                "admitted": [],
                "skipped": skipped,
                "open_before": open_symbols,
                "slots": 0,
                "target_open": len(open_symbols),
                "dynamic": self.dynamic_enabled,
                "cooloff_blocked": cooloff_skipped,
                "min_hybrid_abs_effective": eff_min,
                "adaptive_delta": adaptive_delta,
                "rolling_wr": wr,
                "max_open_effective": hard_cap,
                "morning_session": True,
                "morning_entries_used": morning_entries_used,
                "morning_remaining": 0,
                "skip_log": skip_entry_log().snapshot(),
                "reason": reason,
            }

        selected = select_entries_for_empty_slots(
            [
                EntryCandidate(p.symbol, p.hybrid_score, p.confidence)
                for p in pending.values()
            ],
            open_symbols=open_symbols,
            max_open_symbols=hard_cap,
            min_hybrid_abs=eff_min,
            floor=self.floor,
            ceiling=ceiling,
            dynamic=self.dynamic_enabled,
            cooloff_symbols=cooloff_blocked,
            max_new_entries=morning_remaining,
            admit_top_pct=admit_top_pct,
        )
        admitted_syms = {c.symbol.upper() for c in selected}
        skipped = sorted(
            set(index_skipped)
            | set(cooloff_skipped)
            | {s for s in pending if s not in admitted_syms}
        )
        slots = max(0, hard_cap - len(open_symbols))
        if self.dynamic_enabled:
            # Effective free slots used this cycle (may be < hard-cap free).
            slots = len(selected)

        logger.info(
            "Entry selection anti_churn=%s dynamic=%s floor=%d ceiling=%d "
            "min_|hybrid|=%.2f (base=%.2f Δ=%.2f) top_pct=%s hard_cap=%d wr=%s "
            "cooloff=%s/%dm morning=%s used=%d remaining=%s open=%d "
            "candidates=%d admitted=%s skipped=%d cooloff_blocked=%d",
            self.anti_churn,
            self.dynamic_enabled,
            self.floor,
            ceiling,
            eff_min,
            self.min_hybrid_abs,
            adaptive_delta,
            (
                f"{admit_top_pct:.0%}"
                if admit_top_pct is not None
                else "off"
            ),
            hard_cap,
            f"{wr:.0%}({wr_wins}/{wr_n})" if wr is not None else "n/a",
            self.cooloff_enabled,
            self.cooloff_minutes,
            in_morning,
            morning_entries_used,
            morning_remaining if morning_remaining is not None else "-",
            len(open_symbols),
            len(pending) + len(cooloff_skipped),
            [c.symbol for c in selected],
            len(skipped),
            len(cooloff_skipped),
        )
        if skipped:
            logger.info(
                "Entry selection skipped "
                "(index / cooloff / below |hybrid| / weaker / no slots / morning budget): %s",
                ", ".join(
                    (
                        f"{s}|index"
                        if s in index_skipped
                        else (
                            f"{s}|cooloff"
                            if s in cooloff_skipped
                            else f"{s}|h|={abs(pending[s].hybrid_score):.3f}"
                        )
                    )
                    for s in skipped[:30]
                )
                + ("..." if len(skipped) > 30 else ""),
            )

        router = SafeOrderRouter(halt=bool((cfg.get("trading") or {}).get("halt")))
        gmail = build_gmail_notifier(cfg)
        executed: list[str] = []

        for cand in selected:
            row = pending[cand.symbol.upper()]
            # Ensure risk gate hard-cap matches WR-aware ceiling during deferred fill.
            row_limits = dict(row.limits)
            row_limits["max_open_symbols"] = hard_cap
            row = PendingEntry(
                symbol=row.symbol,
                hybrid_score=row.hybrid_score,
                confidence=row.confidence,
                intents=row.intents,
                safe_mode=row.safe_mode,
                limits=row_limits,
                recommendation=row.recommendation,
            )
            ok = await _execute_pending_entry(
                row, paper=paper, router=router, gmail=gmail
            )
            if ok:
                executed.append(cand.symbol)

        return {
            "admitted": executed,
            "skipped": skipped,
            "open_before": open_symbols,
            "slots": slots,
            "dynamic": self.dynamic_enabled,
            "floor": self.floor,
            "ceiling": ceiling,
            "min_hybrid_abs": self.min_hybrid_abs,
            "min_hybrid_abs_effective": eff_min,
            "adaptive_delta": adaptive_delta,
            "admit_top_pct": admit_top_pct,
            "rolling_wr": wr,
            "rolling_wr_trips": wr_n,
            "max_open_effective": hard_cap,
            "cooloff_blocked": cooloff_skipped,
            "morning_session": in_morning,
            "morning_entries_used": morning_entries_used,
            "morning_remaining": morning_remaining,
            "anti_churn": self.anti_churn,
            "skip_log": skip_entry_log().snapshot(),
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
