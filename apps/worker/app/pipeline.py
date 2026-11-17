"""15m pipeline tick — equity dry-run auto-order orchestration."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from options_portal_broker.adapter import UpstoxAdapter
from options_portal_broker.instruments import resolve_instrument_key
from options_portal_domain.calendar import is_nse_trading_day
from options_portal_domain.config import load_config, trading_mode
from options_portal_domain.equity import plan_equity_intents, resolve_equity_mark
from options_portal_domain.session_gates import (
    MarketSentimentPolicy,
    SoftEodPolicy,
    compute_index_bias,
    entry_conflicts_with_index,
    filter_entry_intents,
    soft_flatten_decision,
)
from options_portal_domain.tz import now_ist
from options_portal_domain.types import SignalAction
from options_portal_hybrid.scorer import WeightedHybridScorer
from options_portal_notify.factory import build_gmail_notifier, build_notifier
from options_portal_risk.order_router import SafeOrderRouter
from options_portal_sentiment.vader_scorer import VaderScorer
from options_portal_ta.indicators import EMAPlugin, RSIPlugin, SupertrendPlugin, VWAPPlugin
from options_portal_ta.registry import IndicatorRegistry
from options_portal_ta.signal_generator import ConfluenceSignalGenerator

logger = logging.getLogger(__name__)

# Last hybrid scores for index symbols — light market_sentiment gate (process-local).
_INDEX_HYBRID_CACHE: dict[str, float] = {}


def get_index_hybrid_cache() -> dict[str, float]:
    return dict(_INDEX_HYBRID_CACHE)


def clear_index_hybrid_cache() -> None:
    _INDEX_HYBRID_CACHE.clear()


def _remember_index_hybrid(symbol: str, hybrid_score: float, policy: MarketSentimentPolicy) -> None:
    sym = symbol.upper()
    if sym in policy.symbols:
        _INDEX_HYBRID_CACHE[sym] = float(hybrid_score)


def build_ta_registry() -> IndicatorRegistry:
    reg = IndicatorRegistry()
    reg.register(RSIPlugin(period=14))
    reg.register(EMAPlugin(fast=9, slow=21))
    reg.register(SupertrendPlugin())
    reg.register(VWAPPlugin())
    return reg


def _equity_share_qty(cfg: dict[str, Any], risk_cfg: dict[str, Any]) -> int:
    equity_cfg = cfg.get("trading", {}).get("equity") or {}
    raw = equity_cfg.get("share_qty", risk_cfg.get("equity_share_qty", 1))
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1


def _equity_enabled(cfg: dict[str, Any]) -> bool:
    equity_cfg = cfg.get("trading", {}).get("equity") or {}
    return bool(equity_cfg.get("enabled", True))


async def run_symbol_tick(symbol: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run one dry-run pipeline tick with equity paper auto-orders."""
    cfg = config or load_config()
    hybrid_cfg = cfg.get("hybrid", {})
    risk_cfg = dict(cfg.get("trading", {}).get("risk", {}) or {})
    equity_cfg = cfg.get("trading", {}).get("equity") or {}
    mode = trading_mode(cfg)  # type: ignore[arg-type]
    share_qty = _equity_share_qty(cfg, risk_cfg)
    # Expose share qty to risk gate as max entry size default
    risk_cfg.setdefault("equity_share_qty", share_qty)
    risk_cfg.setdefault(
        "max_equity_qty_per_symbol",
        int(equity_cfg.get("max_qty_per_symbol", risk_cfg.get("max_equity_qty_per_symbol", share_qty))),
    )

    broker = UpstoxAdapter()
    registry = build_ta_registry()
    signal_gen = ConfluenceSignalGenerator()
    scorer = WeightedHybridScorer(
        weight_tech=float(hybrid_cfg.get("weights", {}).get("tech", 0.65)),
        weight_news=float(hybrid_cfg.get("weights", {}).get("news", 0.35)),
        enter_long=float(hybrid_cfg.get("enter_long", 0.45)),
        enter_short=float(hybrid_cfg.get("enter_short", -0.45)),
        news_veto_abs=float(hybrid_cfg.get("news_veto_abs", 0.70)),
    )
    router = SafeOrderRouter(broker=broker, halt=bool(cfg.get("trading", {}).get("halt")))
    # Console/telegram/webhook only — Gmail is fill + 16:00 IST daily (never HOLD ticks)
    notifier = build_notifier(cfg)
    gmail = build_gmail_notifier(cfg)
    sentiment = VaderScorer()

    end = datetime.now(tz=timezone.utc)
    history_days = int(cfg.get("broker", {}).get("candle_history_days", 5))
    # V3 minutes≤15: max ~1 month; keep short for scan throughput
    start = end - timedelta(days=min(max(history_days, 2), 10))
    candles = await broker.get_candles(symbol, "15m", start, end)
    market_data_source = broker.last_candle_source
    features = registry.compute_all(candles)
    tech = signal_gen.evaluate(symbol, candles, features)

    # Live news from SQLite when available (RSS poller persists on its loop)
    lookback = int(hybrid_cfg.get("news_lookback_minutes", 180))
    news_items: list = []
    sentiments = []
    try:
        from options_portal_persistence.news_store import NewsStore

        store = NewsStore()
        if store.available:
            news_items, sentiments = store.recent_for_symbol(
                symbol, lookback_minutes=lookback, limit=20
            )
    except Exception:  # noqa: BLE001
        logger.exception("news load failed for %s — continuing with empty news", symbol)
        news_items, sentiments = [], []

    if not sentiments and news_items:
        sentiments = list(await sentiment.score_batch(news_items))
    decision = scorer.combine(tech, sentiments, news_items, {"session": "regular"})

    result: dict[str, Any] = {
        "symbol": symbol,
        "tech_action": tech.action.value,
        "hybrid_action": decision.action.value,
        "hybrid_score": decision.hybrid_score,
        "tech_score": decision.tech_score,
        "news_score": decision.news_score,
        "confidence": decision.confidence,
        "reasons": list(decision.reasons),
        "reason_kind": decision.reason_kind,
        "reason_summary": decision.reason_summary,
        "reason_detail": decision.reason_detail,
        "mode": mode,
        "instrument": "equity",
        "execution": "dry_run",
        "market_data_source": market_data_source,
        "candle_count": len(candles),
    }

    candle_close = float(candles[-1].close) if candles else 0.0
    result["last_price"] = candle_close

    if not _equity_enabled(cfg):
        result["order"] = None
        result["note"] = "equity auto-order disabled"
        return result

    # Paper ledger for open positions + risk portfolio snapshot
    paper = None
    try:
        from options_portal_persistence.paper_trades import PaperTradeStore

        paper = PaperTradeStore()
    except Exception:  # noqa: BLE001
        logger.exception("paper ledger init failed for %s", symbol)

    open_side = None
    open_qty = 0
    entry_avg: float | None = None
    portfolio: dict[str, Any] = {}
    if paper is not None and paper.available:
        safe_mode_for_ledger = "dry_run" if mode == "live" else mode
        portfolio = paper.portfolio_snapshot(mode=safe_mode_for_ledger)  # type: ignore[arg-type]
        pos = paper.get_position(symbol, mode=safe_mode_for_ledger)  # type: ignore[arg-type]
        if pos:
            open_side = pos.get("side")
            open_qty = int(pos.get("qty") or 0)
            try:
                entry_avg = float(pos.get("avg_price") or 0) or None
            except (TypeError, ValueError):
                entry_avg = None

    soft_policy = SoftEodPolicy.from_config(cfg)
    mkt_policy = MarketSentimentPolicy.from_config(cfg)
    _remember_index_hybrid(symbol, decision.hybrid_score, mkt_policy)
    ist_now = now_ist()
    trading_day = is_nse_trading_day(ist_now.date())
    result["trading_day"] = trading_day
    result["session"] = {
        "soft_flatten_ist": soft_policy.soft_flatten_ist,
        "no_new_entries_after": soft_policy.no_new_entries_after,
        "close_ist": soft_policy.close_ist,
        "user_hours_note": soft_policy.user_hours_note,
        "past_soft_flatten": soft_policy.past_soft_flatten(ist_now),
        "past_no_new_entries": soft_policy.past_no_new_entries(ist_now),
        "trading_day": trading_day,
    }

    # Weekends / NSE holidays: no soft_flatten, no paper orders, no fill emails
    if not trading_day:
        note = (
            f"non_trading_day={ist_now.date().isoformat()} — "
            "skip soft_flatten and equity dry-run orders"
        )
        logger.info("%s %s", symbol, note)
        result["order"] = None
        result["note"] = note
        result["soft_eod"] = {
            "active": False,
            "action": "none",
            "reason": note,
        }
        return result

    reason_summary = decision.reason_summary or decision.action.value
    flatten = soft_flatten_decision(
        open_side=open_side,  # type: ignore[arg-type]
        open_qty=open_qty,
        hybrid_score=decision.hybrid_score,
        policy=soft_policy,
        now=ist_now,
    )
    result["soft_eod"] = flatten

    if flatten["action"] == "overnight_hold":
        logger.info("%s %s", symbol, flatten["reason"])
        intents = []
        result["order"] = None
        result["note"] = flatten["reason"]
        result["overnight_hold"] = True
        await notifier.send(
            f"{symbol} overnight hold",
            flatten["reason"],
            {"hybrid_score": decision.hybrid_score, "open_side": open_side},
        )
        return result

    # Live LTP for THIS symbol only (entry / exit / soft_flatten / cover).
    # Historical candle close alone can be stale and equal entry → +0 PnL.
    # Never reuse another symbol's quote / entry avg / synthetic close.
    # Fetch only when we may place an order (skip idle HOLD with no position).
    may_trade = (
        flatten["action"] == "flatten"
        or decision.action != SignalAction.HOLD
        or (open_qty > 0 and open_side is not None)
    )
    instrument_key = resolve_instrument_key(
        symbol, overrides=getattr(broker, "instrument_overrides", None)
    )
    result["instrument_key"] = instrument_key
    ltp_px: float | None = None
    quote_instrument_key: str | None = None
    if may_trade:
        try:
            quote = await broker.get_ltp(symbol)
            if quote and quote.get("last_price") is not None:
                # Guard: adapter must echo the requested symbol
                q_sym = str(quote.get("symbol") or "").strip().upper()
                q_key = str(quote.get("instrument_key") or "").strip()
                if q_sym and q_sym != symbol.upper():
                    logger.error(
                        "%s get_ltp returned mismatched symbol=%s instrument_key=%s — ignoring",
                        symbol,
                        q_sym,
                        q_key or instrument_key,
                    )
                elif (
                    instrument_key
                    and q_key
                    and q_key != instrument_key
                ):
                    logger.error(
                        "%s get_ltp instrument_key mismatch want=%s got=%s — ignoring",
                        symbol,
                        instrument_key,
                        q_key,
                    )
                else:
                    raw_ltp = float(quote["last_price"])
                    if raw_ltp > 0:
                        ltp_px = raw_ltp
                        quote_instrument_key = q_key or instrument_key
        except Exception:  # noqa: BLE001
            logger.exception(
                "%s get_ltp failed instrument_key=%s while resolving equity mark",
                symbol,
                instrument_key,
            )

    # Live candle close only when Upstox returned bars for THIS instrument_key.
    # Synthetic closes (missing key / HTTP failure) must not price paper fills
    # when a live token is configured — that path previously shared ~996.96.
    live_candle_close: float | None = None
    if market_data_source == "upstox" and candle_close > 0:
        live_candle_close = candle_close
    elif candle_close > 0 and not broker.credentials_configured():
        # No token: synthetic / fixture candles are the only MD (local demo)
        live_candle_close = candle_close

    mark = resolve_equity_mark(
        ltp=ltp_px,
        candle_close=live_candle_close,
        # entry_avg from get_position(symbol) — never a global last-fill price
        entry_avg=entry_avg if open_qty > 0 else None,
    )
    fill_price = mark.price
    result["mark_price"] = fill_price
    result["mark_source"] = mark.source
    result["quote_source"] = getattr(broker, "last_quote_source", mark.source)
    result["mark_instrument_key"] = quote_instrument_key or instrument_key
    if may_trade:
        logger.log(
            logging.INFO if mark.source == "ltp" else logging.WARNING,
            "%s equity mark source=%s price=%.4f instrument_key=%s "
            "candle_source=%s quote_source=%s detail=%s",
            symbol,
            mark.source,
            fill_price,
            quote_instrument_key or instrument_key or "UNRESOLVED",
            market_data_source,
            getattr(broker, "last_quote_source", "?"),
            mark.detail or mark.source,
        )
        # Token present but still on synthetic/entry fallback → skip paper order.
        # Prevents IOC (etc.) from printing another name's LTP or a shared fake close.
        if broker.credentials_configured() and mark.source not in {"ltp", "candle_close"}:
            note = (
                f"refusing paper fill: mark_source={mark.source} "
                f"instrument_key={instrument_key or 'UNRESOLVED'} "
                f"(need this symbol's LTP or live candle close)"
            )
            logger.error("%s %s", symbol, note)
            result["order"] = None
            result["note"] = note
            return result
        if (
            broker.credentials_configured()
            and mark.source == "candle_close"
            and market_data_source != "upstox"
        ):
            note = (
                f"refusing paper fill on synthetic candle mark "
                f"instrument_key={instrument_key or 'UNRESOLVED'}"
            )
            logger.error("%s %s", symbol, note)
            result["order"] = None
            result["note"] = note
            return result

    if flatten["action"] == "flatten":
        logger.info("%s %s", symbol, flatten["reason"])
        intents = plan_equity_intents(
            symbol,
            SignalAction.HOLD,
            open_side=open_side,  # type: ignore[arg-type]
            open_qty=open_qty,
            qty=share_qty,
            price=fill_price,
            reason=flatten["reason"],
        )
    else:
        intents = plan_equity_intents(
            symbol,
            decision.action,
            open_side=open_side,  # type: ignore[arg-type]
            open_qty=open_qty,
            qty=share_qty,
            price=fill_price,
            reason=reason_summary,
        )

    # Soft cut-off: no new entries after configured IST time (exits still allowed)
    if soft_policy.past_no_new_entries(ist_now):
        before = len(intents)
        intents = filter_entry_intents(intents, allow_entries=False)
        if before and not intents:
            logger.info(
                "%s no_new_entries_after=%s — blocked entry intents",
                symbol,
                soft_policy.no_new_entries_after,
            )
            result["order"] = None
            result["note"] = (
                f"no_new_entries_after={soft_policy.no_new_entries_after} IST"
            )
            return result
        if len(intents) < before:
            result["note"] = (
                f"no_new_entries_after={soft_policy.no_new_entries_after} "
                "— entry legs dropped; exits kept"
            )

    # Light index gate: block new opposite entries when NIFTY/BANKNIFTY strongly conflict
    if mkt_policy.enabled and any(
        getattr(i, "action_kind", None) in {"entry", "flip_open"} for i in intents
    ):
        index_info = compute_index_bias(
            _INDEX_HYBRID_CACHE,
            symbols=mkt_policy.symbols,
            disagree_min_abs=mkt_policy.disagree_min_abs,
        )
        result["market_sentiment"] = index_info
        blocked, block_reason = entry_conflicts_with_index(
            decision.action,
            index_info.get("bias"),
            conflict_abs=mkt_policy.conflict_abs,
        )
        if blocked:
            logger.info("%s %s", symbol, block_reason)
            intents = filter_entry_intents(intents, allow_entries=False)
            # Keep flip_close / exit if present; if only entries were planned, stop
            if not intents:
                result["order"] = None
                result["note"] = block_reason
                return result
            result["note"] = block_reason
        else:
            logger.debug("%s market_sentiment ok: %s", symbol, block_reason)

    if decision.action == SignalAction.HOLD and not intents:
        logger.info(
            "%s HOLD hybrid=%.3f reason=%s",
            symbol,
            decision.hybrid_score,
            decision.reason_summary,
        )
        await notifier.send(
            f"{symbol} HOLD",
            f"hybrid={decision.hybrid_score} ({decision.reason_summary})",
            {
                "reasons": decision.reasons,
                "reason_kind": decision.reason_kind,
                "reason_detail": decision.reason_detail,
            },
        )
        return result

    if not intents:
        result["order"] = None
        result["note"] = "no equity intent (already positioned or no action)"
        return result

    # Force dry_run path for safety even if config says otherwise in worker
    safe_mode = "dry_run" if mode == "live" else mode
    limits = {**risk_cfg, "halt": cfg.get("trading", {}).get("halt")}
    order_rows: list[dict[str, Any]] = []

    for intent in intents:
        # Refresh portfolio between flip_close and flip_open
        if paper is not None and paper.available:
            portfolio = paper.portfolio_snapshot(mode=safe_mode)  # type: ignore[arg-type]

        order = await router.execute_equity(
            intent,
            safe_mode,  # type: ignore[arg-type]
            actor_user_id=None,
            portfolio=portfolio,
            limits=limits,
        )
        persist_meta: dict[str, Any] = {}
        if (
            paper is not None
            and paper.available
            and order.status == "simulated"
        ):
            persist_meta = paper.record_simulated_fill(
                intent, order, mode=safe_mode  # type: ignore[arg-type]
            )
            # Email on each paper/dry-run fill (Gmail when GMAIL_APP_PASSWORD set)
            if persist_meta.get("persisted") and gmail is not None and gmail.enabled:
                try:
                    gmail.send_fill(
                        {
                            "symbol": intent.symbol,
                            "side": intent.side,
                            "qty": intent.qty,
                            "price": intent.limit_price,
                            "reason": intent.reason,
                            "action_kind": intent.action_kind,
                            "ts_ist_display": persist_meta.get("ts_ist_display"),
                        },
                        mode=str(safe_mode),
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("Gmail fill notify failed for %s", intent.symbol)

        order_rows.append(
            {
                "status": order.status,
                "mode": order.mode,
                "client_order_id": order.client_order_id,
                "message": order.message,
                "side": intent.side,
                "qty": intent.qty,
                "price": intent.limit_price,
                "reason": intent.reason,
                "action_kind": intent.action_kind,
                "dry_run": True,
                "instrument": "equity",
                "persisted": persist_meta.get("persisted", False),
                "ts_ist_display": persist_meta.get("ts_ist_display"),
            }
        )

    result["orders"] = order_rows
    result["order"] = order_rows[-1] if order_rows else None
    result["recommendation"] = (
        f"Equity dry_run: {decision.action.value} → "
        + "; ".join(f"{o['side']} {o['qty']} @ {o['price']:.2f}" for o in order_rows)
    )
    await notifier.send(
        f"{symbol} {decision.action.value} equity",
        result["recommendation"],
        {"orders": order_rows},
    )
    return result
