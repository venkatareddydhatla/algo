"""15m pipeline tick — equity dry-run auto-order orchestration."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from options_portal_broker.adapter import UpstoxAdapter
from options_portal_broker.instruments import resolve_instrument_key
from options_portal_domain.adaptive_hybrid import load_adaptive_delta
from options_portal_domain.calendar import is_nse_trading_day, pnl_ist_day
from options_portal_domain.candles_agg import (
    aggregate_15m_to_1h,
    bar_direction,
)
from options_portal_domain.config import load_config, trading_mode
from options_portal_domain.entry_selection import (
    entry_selection_config,
    in_reentry_cooloff,
    last_exit_times_from_fills,
)
from options_portal_domain.equity import (
    below_min_equity_price,
    last_good_ltp,
    min_equity_price_from_config,
    plan_equity_intents,
    remember_last_good_ltp,
    resolve_equity_mark,
    risk_exit_mark_accepted,
    session_mark_bounds,
)
from options_portal_domain.high_conviction import (
    evaluate_entry_gate,
    high_conviction_config,
    last_trade_times_from_fills,
    rolling_equity_win_rate,
    skip_entry_log,
)
from options_portal_domain.pine_signals import (
    evaluate_pine_signal,
    pine_params_from_config,
    pine_signals_enabled,
    prefer_closed_candles,
    watchlist_pine_action,
)
from options_portal_domain.session_gates import (
    AtrTrailScaleOutPolicy,
    FixedPctExitPolicy,
    MarketSentimentPolicy,
    MorningProfitBookPolicy,
    OppositePineExitPolicy,
    ProfitCandleProtectPolicy,
    SoftEodPolicy,
    allow_open_book_risk_exits,
    atr_trail_scale_out_decision,
    compute_index_bias,
    entry_conflicts_with_index,
    filter_entry_intents,
    fixed_pct_exit_decision,
    is_trading_session,
    morning_profit_book_decision,
    opposite_pine_exit_decision,
    profit_candle_protect_decision,
    soft_flatten_decision,
)
from options_portal_domain.tz import now_ist
from options_portal_domain.types import HybridDecision, SignalAction
from options_portal_domain.universe import is_index_underlying
from options_portal_hybrid.scorer import WeightedHybridScorer
from options_portal_notify.factory import build_gmail_notifier, build_notifier
from options_portal_risk.order_router import SafeOrderRouter
from options_portal_sentiment.vader_scorer import VaderScorer
from options_portal_ta.indicators import (
    EMAPlugin,
    RSIPlugin,
    SupertrendPlugin,
    VWAPPlugin,
    atr as atr_indicator,
)
from options_portal_ta.registry import IndicatorRegistry
from options_portal_ta.signal_generator import ConfluenceSignalGenerator

logger = logging.getLogger(__name__)

# Last hybrid scores for index symbols — light market_sentiment gate (process-local).
_INDEX_HYBRID_CACHE: dict[str, float] = {}

# Per full-scan |hybrid| abs scores — top-10% score-filter OR (process-local).
_SCAN_HYBRID_ABS: dict[str, float] = {}

# ATR trail extreme / stop per symbol (process-local; re-seeds after worker restart).
_ATR_TRAIL_STATE: dict[str, dict[str, Any]] = {}


def get_index_hybrid_cache() -> dict[str, float]:
    return dict(_INDEX_HYBRID_CACHE)


def clear_index_hybrid_cache() -> None:
    _INDEX_HYBRID_CACHE.clear()


def remember_scan_hybrid_abs(symbol: str, hybrid_score: float) -> None:
    """Record |hybrid| for the current full-universe scan (top-pct OR path)."""
    sym = str(symbol or "").strip().upper()
    if not sym:
        return
    _SCAN_HYBRID_ABS[sym] = abs(float(hybrid_score))


def scan_hybrid_abs_scores() -> list[float]:
    return list(_SCAN_HYBRID_ABS.values())


def clear_scan_hybrid_abs() -> None:
    _SCAN_HYBRID_ABS.clear()


def get_atr_trail_state(symbol: str) -> dict[str, Any]:
    """Mutable per-symbol trail state (activated / extreme / trail_stop)."""
    key = symbol.upper()
    if key not in _ATR_TRAIL_STATE:
        _ATR_TRAIL_STATE[key] = {
            "activated": False,
            "extreme": None,
            "trail_stop": None,
        }
    return _ATR_TRAIL_STATE[key]


def clear_atr_trail_state(symbol: str | None = None) -> None:
    """Clear one symbol (or all) after flat / for tests."""
    if symbol is None:
        _ATR_TRAIL_STATE.clear()
        return
    _ATR_TRAIL_STATE.pop(symbol.upper(), None)


def _remember_index_hybrid(symbol: str, hybrid_score: float, policy: MarketSentimentPolicy) -> None:
    sym = symbol.upper()
    if sym in policy.symbols:
        _INDEX_HYBRID_CACHE[sym] = float(hybrid_score)


def build_ta_registry() -> IndicatorRegistry:
    reg = IndicatorRegistry()
    reg.register(RSIPlugin(period=14))
    # Align EMA lengths with Pine defaults (13/21) for legacy confluence fallback.
    reg.register(EMAPlugin(fast=13, slow=21))
    reg.register(SupertrendPlugin(atr_period=10, multiplier=3.0))
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


async def run_symbol_tick(
    symbol: str,
    config: dict[str, Any] | None = None,
    *,
    entry_admission: Any | None = None,
) -> dict[str, Any]:
    """Run one dry-run pipeline tick with equity paper auto-orders.

    When ``entry_admission`` is set (full-universe worker batch), new ``entry``
    intents are staged and ranked by |hybrid| at cycle end instead of
    CSV/scan first-fill. Exits, soft flatten, and flips still execute immediately.
    """
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
    use_pine = pine_signals_enabled(cfg)
    pine_params = pine_params_from_config(cfg)
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
    min_equity_price = min_equity_price_from_config(cfg)
    candle_close_early = float(candles[-1].close) if candles else None

    # ₹20 floor: skip watchlist scoring + new entries when flat and cheap.
    # Open positions still continue for exit management below.
    paper_peek = None
    peek_open = False
    try:
        from options_portal_persistence.paper_trades import PaperTradeLedger

        paper_peek = PaperTradeLedger()
        if paper_peek.available:
            safe_peek = "dry_run" if mode == "live" else mode
            pos_peek = paper_peek.get_position(symbol, mode=safe_peek)  # type: ignore[arg-type]
            if pos_peek and int(pos_peek.get("qty") or 0) > 0:
                peek_open = True
    except Exception:  # noqa: BLE001
        logger.exception("%s price_floor paper peek failed — continuing", symbol)
        peek_open = False

    if (
        not peek_open
        and below_min_equity_price(candle_close_early, min_price=min_equity_price)
    ):
        note = (
            f"price_floor — close/LTP {float(candle_close_early):.2f} < "
            f"₹{min_equity_price:g} — skip watchlist score and new entry"
        )
        logger.info("%s %s", symbol, note)
        # Drop stale pine.signal KV so watchlist / TOP BUY cannot show this name.
        pine_kv_cleared = False
        try:
            from options_portal_persistence.kv_store import KvStore

            kv = KvStore()
            if kv.available:
                pine_kv_cleared = bool(kv.delete(f"pine.signal.{symbol.upper()}"))
                if pine_kv_cleared:
                    logger.info(
                        "%s cleared pine.signal KV (price_floor)", symbol.upper()
                    )
        except Exception:  # noqa: BLE001
            logger.exception("%s pine.signal KV clear failed", symbol)
        return {
            "symbol": symbol.upper(),
            "hybrid_action": SignalAction.HOLD.value,
            "hybrid_score": 0.0,
            "order": None,
            "note": note,
            "price_floor": True,
            "min_equity_price": min_equity_price,
            "candle_close": candle_close_early,
            "market_data_source": market_data_source,
            "pine_kv_cleared": pine_kv_cleared,
        }

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

    pine_result = None
    if use_pine:
        pine_result = evaluate_pine_signal(
            symbol, candles, params=pine_params, cfg=cfg
        )
        closed_for_bar = prefer_closed_candles(candles)
        bar_ts = (
            closed_for_bar[-1].ts
            if closed_for_bar
            else (candles[-1].ts if candles else datetime.now(tz=timezone.utc))
        )
        # Pine-only decision path: Action = BUY/SELL/HOLD; score ±1/0 for soft EOD.
        decision = HybridDecision(
            symbol=symbol.upper(),
            action=pine_result.action,
            tech_score=float(pine_result.score),
            news_score=0.0,
            hybrid_score=float(pine_result.score),
            confidence=float(pine_result.confidence),
            reasons=list(pine_result.reasons),
            news_ids=[],
            bar_ts=bar_ts,
            reason_kind="pine",
            reason_summary=pine_result.reason_summary,
            reason_detail=pine_result.reason_detail,
        )
        features = {**features, **pine_result.features}
        logger.info(
            "%s pine_signal action=%s buy=%s sell=%s prior_x=%s prior_xu=%s "
            "clear_above=%s clear_below=%s",
            symbol,
            pine_result.action.value,
            pine_result.buy_signal,
            pine_result.sell_signal,
            (pine_result.state.prior_crossover if pine_result.state else None),
            (pine_result.state.prior_crossunder if pine_result.state else None),
            (pine_result.state.candle_clear_above if pine_result.state else None),
            (pine_result.state.candle_clear_below if pine_result.state else None),
        )
        # Persist watchlist Action only while signal bar is current / same 15m;
        # otherwise HOLD so names like APOLLOHOSP cannot linger as SELL for hours.
        try:
            from options_portal_persistence.kv_store import KvStore

            kv = KvStore()
            if kv.available:
                now_utc = datetime.now(tz=timezone.utc)
                bar_ts_iso = (
                    bar_ts.astimezone(timezone.utc).isoformat()
                    if getattr(bar_ts, "tzinfo", None)
                    else bar_ts.replace(tzinfo=timezone.utc).isoformat()
                )
                wl_action = watchlist_pine_action(
                    pine_result.action,
                    bar_ts,
                    now=now_utc,
                    timeframe_minutes=15,
                )
                live = wl_action in {"BUY", "SELL"}
                if live:
                    persist_action = pine_result.action.value
                    persist_buy = bool(pine_result.buy_signal)
                    persist_sell = bool(pine_result.sell_signal)
                    persist_score = float(pine_result.score)
                    persist_conf = float(pine_result.confidence)
                    persist_summary = pine_result.reason_summary
                    persist_detail = pine_result.reason_detail
                    persist_reasons = list(pine_result.reasons)
                else:
                    persist_action = SignalAction.HOLD.value
                    persist_buy = False
                    persist_sell = False
                    persist_score = 0.0
                    persist_conf = 0.0
                    if pine_result.action != SignalAction.HOLD:
                        persist_summary = "stale Pine signal → HOLD"
                        persist_detail = (
                            "Neutral: signal bar no longer current "
                            f"(bar_ts={bar_ts_iso}); watchlist Action expired to HOLD"
                        )
                        persist_reasons = [
                            f"signal bar {bar_ts_iso} expired → HOLD",
                            *list(pine_result.reasons)[:6],
                        ]
                    else:
                        persist_summary = pine_result.reason_summary
                        persist_detail = pine_result.reason_detail
                        persist_reasons = list(pine_result.reasons)
                kv.set(
                    f"pine.signal.{symbol.upper()}",
                    {
                        "symbol": symbol.upper(),
                        "action": persist_action,
                        "buy_signal": persist_buy,
                        "sell_signal": persist_sell,
                        "score": persist_score,
                        "confidence": persist_conf,
                        "reasons": persist_reasons,
                        "reason_kind": "pine",
                        "reason_summary": persist_summary,
                        "reason_detail": persist_detail,
                        "features": {
                            k: v
                            for k, v in pine_result.features.items()
                            if isinstance(v, (int, float, bool, str, type(None)))
                        },
                        "bar_ts": bar_ts_iso,
                        "updated_at": now_utc.isoformat(),
                        "timeframe": "15m",
                    },
                )
        except Exception:  # noqa: BLE001
            logger.exception("%s pine signal persist failed", symbol)
    else:
        decision = scorer.combine(tech, sentiments, news_items, {"session": "regular"})
        # Full-scan |hybrid| abs for top-10% score-filter OR (cleared each batch).
        if entry_admission is not None and not is_index_underlying(symbol):
            remember_scan_hybrid_abs(symbol, float(decision.hybrid_score))

    result: dict[str, Any] = {
        "symbol": symbol,
        "tech_action": (
            pine_result.action.value if pine_result is not None else tech.action.value
        ),
        "hybrid_action": decision.action.value,
        "action": decision.action.value,
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
        "signal_engine": "pine_nifty50_v2" if use_pine else "hybrid_confluence",
        "pine": (
            {
                "buy_signal": pine_result.buy_signal,
                "sell_signal": pine_result.sell_signal,
                "params": {
                    "ema_fast": pine_params.ema_fast,
                    "ema_slow": pine_params.ema_slow,
                    "su_len": pine_params.su_len,
                    "su_mult": pine_params.su_mult,
                    "adx_len": pine_params.adx_len,
                    "vol_ratio": pine_params.vol_ratio,
                },
            }
            if pine_result is not None
            else None
        ),
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
    morning_policy = MorningProfitBookPolicy.from_config(cfg)
    atr_trail_policy = AtrTrailScaleOutPolicy.from_config(cfg)
    fixed_pct_policy = FixedPctExitPolicy.from_config(cfg)
    profit_candle_policy = ProfitCandleProtectPolicy.from_config(cfg)
    opposite_pine_policy = OppositePineExitPolicy.from_config(cfg)
    mkt_policy = MarketSentimentPolicy.from_config(cfg)
    _remember_index_hybrid(symbol, decision.hybrid_score, mkt_policy)
    # Resolve instrument key early for index segment check (NSE_INDEX/BSE_INDEX).
    instrument_key = resolve_instrument_key(
        symbol, overrides=getattr(broker, "instrument_overrides", None)
    )
    result["instrument_key"] = instrument_key
    index_underlying = is_index_underlying(symbol, instrument_key=instrument_key)
    result["index_underlying"] = index_underlying
    ist_now = now_ist()
    trading_day = is_nse_trading_day(ist_now.date())
    result["trading_day"] = trading_day
    entry_session = is_trading_session(
        ist_now, policy=soft_policy, trading_day=trading_day
    )
    risk_exit_session = allow_open_book_risk_exits(
        ist_now, policy=soft_policy, trading_day=trading_day
    )
    result["session"] = {
        "open_ist": soft_policy.open_ist,
        "entries_open_ist": soft_policy.entries_open_ist,
        "soft_flatten_ist": soft_policy.soft_flatten_ist,
        "soft_flatten_until_ist": soft_policy.soft_flatten_until_ist,
        "morning_tp_ist": soft_policy.morning_tp_ist,
        "morning_tp_until_ist": soft_policy.morning_tp_until_ist,
        "in_morning_profit_window": soft_policy.in_morning_profit_window(ist_now),
        "in_risk_exit_window": soft_policy.in_risk_exit_window(ist_now),
        "no_new_entries_after": soft_policy.no_new_entries_after,
        "close_ist": soft_policy.close_ist,
        "user_hours_note": soft_policy.user_hours_note,
        "past_soft_flatten": soft_policy.past_soft_flatten(ist_now),
        "in_soft_flatten_window": soft_policy.in_soft_flatten_window(ist_now),
        "past_soft_flatten_until": soft_policy.past_soft_flatten_until(ist_now),
        "past_no_new_entries": soft_policy.past_no_new_entries(ist_now),
        "before_session_open": soft_policy.before_session_open(ist_now),
        "before_entries_open": soft_policy.before_entries_open(ist_now),
        "allow_new_entries": soft_policy.allow_new_entries(ist_now),
        "is_trading_session": entry_session,
        "allow_open_book_risk_exits": risk_exit_session,
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
    # Index underlyings: never equity entry; soft-close any open paper immediately.
    if index_underlying and (open_side is None or open_qty <= 0):
        note = (
            f"index_not_equity: {symbol} is an index underlying — "
            "skip equity stock entry/exit book (hybrid scored for sentiment only)"
        )
        logger.info("%s %s", symbol, note)
        result["order"] = None
        result["note"] = note
        result["soft_eod"] = {
            "active": False,
            "action": "none",
            "reason": note,
        }
        result["morning_tp"] = {
            "active": False,
            "action": "none",
            "reason": "index_not_equity",
        }
        return result

    # Soft flatten overnight hold requires |h|≥0.85 + 15m/1h trend confirmed.
    bars_1h = aggregate_15m_to_1h(candles) if candles else []
    dir_15m = bar_direction(candles) if candles else "neutral"
    dir_1h = bar_direction(bars_1h) if bars_1h else "neutral"
    trend_ok = False
    if open_side == "long":
        trend_ok = dir_15m == "bullish" and dir_1h == "bullish"
    elif open_side == "short":
        trend_ok = dir_15m == "bearish" and dir_1h == "bearish"
    flatten = soft_flatten_decision(
        open_side=open_side,  # type: ignore[arg-type]
        open_qty=open_qty,
        hybrid_score=decision.hybrid_score,
        policy=soft_policy,
        now=ist_now,
        trend_confirmed=trend_ok,
    )
    result["mtf"] = {"dir_15m": dir_15m, "dir_1h": dir_1h, "trend_confirmed": trend_ok}
    # Index opens win over soft-flatten overnight hold — always clean the book.
    if index_underlying and open_side is not None and open_qty > 0:
        flatten = {
            "active": True,
            "action": "flatten",
            "reason": (
                f"index_not_equity: soft-close open {open_side} "
                f"{symbol} (indices must not stay in equity paper book)"
            ),
        }
    result["soft_eod"] = flatten

    fixed_pct_mgmt_needed = (
        fixed_pct_policy.enabled
        and not index_underlying
        and open_qty > 0
        and open_side is not None
        and risk_exit_session
    )
    profit_candle_mgmt_needed = (
        profit_candle_policy.enabled
        and not index_underlying
        and open_qty > 0
        and open_side is not None
        and risk_exit_session
    )
    atr_mgmt_needed = (
        atr_trail_policy.enabled
        and not index_underlying
        and open_qty > 0
        and open_side is not None
        and risk_exit_session
    )
    morning_mgmt_needed = (
        not index_underlying
        and open_qty > 0
        and open_side is not None
        and morning_policy.enabled
        and morning_policy.in_window(ist_now)
        and risk_exit_session
    )

    # Overnight hold: still fetch mark when fixed-pct/candle/ATR/morning may exit
    # (priority > soft_flatten). Only short-circuit when no exit mgmt can fire.
    if (
        flatten["action"] == "overnight_hold"
        and not fixed_pct_mgmt_needed
        and not profit_candle_mgmt_needed
        and not atr_mgmt_needed
        and not morning_mgmt_needed
    ):
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
        or fixed_pct_mgmt_needed
        or profit_candle_mgmt_needed
        or atr_mgmt_needed
        or morning_mgmt_needed
    )
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

    # Session H/L from today's 15m bars — sanity reject only (never use H/L as
    # the exit mark). Also compare vs last good live LTP so a day-low tick
    # inside the band cannot fire pct_profit_2 (APOLLOHOSP 8738 vs ~9230).
    session_high: float | None = None
    session_low: float | None = None
    if candles:
        bounds = session_mark_bounds(candles, now=ist_now)
        if bounds is not None:
            session_high, session_low = bounds
            result["session_high"] = session_high
            result["session_low"] = session_low

    recent_px = last_good_ltp(symbol)
    mark = resolve_equity_mark(
        ltp=ltp_px,
        candle_close=live_candle_close,
        # entry_avg from get_position(symbol) — never a global last-fill price
        entry_avg=entry_avg if open_qty > 0 else None,
        session_high=session_high,
        session_low=session_low,
        recent_ltp=recent_px,
    )
    fill_price = mark.price
    result["mark_price"] = fill_price
    result["mark_source"] = mark.source
    result["quote_source"] = getattr(broker, "last_quote_source", mark.source)
    result["mark_instrument_key"] = quote_instrument_key or instrument_key
    result["recent_ltp"] = recent_px
    if may_trade:
        logger.log(
            logging.INFO if mark.source == "ltp" else logging.WARNING,
            "%s equity mark source=%s price=%.4f instrument_key=%s "
            "candle_source=%s quote_source=%s recent_ltp=%s detail=%s",
            symbol,
            mark.source,
            fill_price,
            quote_instrument_key or instrument_key or "UNRESOLVED",
            market_data_source,
            getattr(broker, "last_quote_source", "?"),
            f"{recent_px:.4f}" if recent_px else "none",
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

    # Open-book risk exits (±2%, profit_candle, ATR, morning) require live LTP
    # for this equity only, consistent with last good LTP (else entry).
    risk_mark: float | None = None
    risk_ok = False
    risk_note = "risk exits not evaluated"
    if may_trade and open_qty > 0 and open_side is not None:
        risk_ok, risk_note = risk_exit_mark_accepted(
            mark=fill_price if mark.source == "ltp" else None,
            mark_source=mark.source if mark.source == "ltp" else "none",
            recent_ltp=recent_px,
            entry_avg=entry_avg,
        )
        if risk_ok:
            risk_mark = float(fill_price)
            remember_last_good_ltp(symbol, risk_mark)
        else:
            logger.warning("%s skipping risk exits: %s", symbol, risk_note)
            # Never seed last-good from a rejected / inconsistent mark.
    elif may_trade and mark.source == "ltp" and fill_price > 0:
        remember_last_good_ltp(symbol, float(fill_price))
    result["risk_exit_mark_ok"] = risk_ok
    result["risk_exit_mark_note"] = risk_note

    atr14: float | None = None
    hc_cfg = high_conviction_config(cfg)
    atr_period = (
        atr_trail_policy.atr_period
        if atr_mgmt_needed
        else (
            int(hc_cfg.get("atr_period", morning_policy.atr_period))
            if hc_cfg.get("enabled", True)
            else morning_policy.atr_period
        )
    )
    # Always compute ATR when candles exist — entry gate needs ATR% + proximity.
    if candles and (
        atr_mgmt_needed
        or morning_mgmt_needed
        or (hc_cfg.get("enabled", True) and entry_session and not index_underlying)
    ):
        try:
            atr14 = atr_indicator(candles, atr_period)
        except Exception:  # noqa: BLE001
            logger.exception(
                "%s ATR%d compute failed", symbol, atr_period
            )
            atr14 = None
    result["atr14"] = atr14

    fixed_pct = fixed_pct_exit_decision(
        open_side=open_side,  # type: ignore[arg-type]
        open_qty=open_qty,
        entry_avg=entry_avg,
        mark=risk_mark,
        policy=fixed_pct_policy,
        now=ist_now,
        is_index=index_underlying,
        session_policy=soft_policy,
        trading_day=trading_day,
    )
    if not risk_ok and open_qty > 0 and open_side is not None:
        fixed_pct = {
            **fixed_pct,
            "active": True,
            "action": "none",
            "reason": f"fixed_pct_exit skipped: {risk_note}",
            "exit_qty": 0,
        }
    result["fixed_pct_exit"] = fixed_pct

    profit_candle = profit_candle_protect_decision(
        open_side=open_side,  # type: ignore[arg-type]
        open_qty=open_qty,
        entry_avg=entry_avg,
        mark=risk_mark,
        candles=candles,
        policy=profit_candle_policy,
        now=ist_now,
        is_index=index_underlying,
        session_policy=soft_policy,
        trading_day=trading_day,
    )
    if not risk_ok and open_qty > 0 and open_side is not None:
        profit_candle = {
            **profit_candle,
            "active": True,
            "action": "none",
            "reason": f"profit_candle_protect skipped: {risk_note}",
            "exit_qty": 0,
        }
    result["profit_candle_protect"] = profit_candle

    trail_state = get_atr_trail_state(symbol) if atr_mgmt_needed else None
    atr_mgmt = atr_trail_scale_out_decision(
        open_side=open_side,  # type: ignore[arg-type]
        open_qty=open_qty,
        entry_avg=entry_avg,
        mark=risk_mark,
        policy=atr_trail_policy,
        atr14=atr14,
        is_index=index_underlying,
        trail_state=trail_state,
        now=ist_now,
        session_policy=soft_policy,
        trading_day=trading_day,
    )
    if not risk_ok and open_qty > 0 and open_side is not None:
        atr_mgmt = {
            **atr_mgmt,
            "action": "none",
            "reason": f"atr_trail skipped: {risk_note}",
            "exit_qty": 0,
        }
    result["atr_trail"] = atr_mgmt

    morning_tp = morning_profit_book_decision(
        open_side=open_side,  # type: ignore[arg-type]
        open_qty=open_qty,
        entry_avg=entry_avg,
        mark=risk_mark,
        policy=morning_policy,
        now=ist_now,
        is_index=index_underlying,
        atr14=atr14,
        session_policy=soft_policy,
        trading_day=trading_day,
    )
    if not risk_ok and open_qty > 0 and open_side is not None:
        morning_tp = {
            **morning_tp,
            "action": "none",
            "reason": f"morning_tp skipped: {risk_note}",
            "exit_qty": 0,
        }
    result["morning_tp"] = morning_tp

    # Opposite fresh Pine BUY/SELL → close-only (no same-bar flip). Freshness
    # matches watchlist expire-stale (signal bar current / latest closed 15m).
    pine_bar_ts = getattr(decision, "bar_ts", None) if use_pine else None
    opposite_pine = opposite_pine_exit_decision(
        open_side=open_side,  # type: ignore[arg-type]
        open_qty=open_qty,
        pine_action=decision.action if use_pine else SignalAction.HOLD,
        bar_ts=pine_bar_ts,
        policy=opposite_pine_policy,
        now=datetime.now(tz=timezone.utc),
        timeframe_minutes=15,
        is_index=index_underlying,
    )
    if not use_pine:
        opposite_pine = {
            **opposite_pine,
            "active": False,
            "action": "none",
            "reason": "opposite_pine_exit skipped: hybrid path",
            "exit_qty": 0,
        }
    result["opposite_pine_exit"] = opposite_pine

    # Same-tick priority: fixed ±2% > profit_candle_protect > atr stop/trail >
    # scale/profit > morning > opposite_pine > soft_flatten. ATR/morning stay
    # wired but off.
    atr_exit_actions = {
        "atr_stop",
        "atr_trail",
        "profit_book",
        "profit_scale_out",
    }
    fixed_pct_action = str(fixed_pct.get("action") or "none")
    profit_candle_action = str(profit_candle.get("action") or "none")
    opposite_pine_action = str(opposite_pine.get("action") or "none")
    if fixed_pct_action not in {"none", ""} and fixed_pct.get("exit_qty"):
        logger.info("%s %s", symbol, fixed_pct["reason"])
        intents = plan_equity_intents(
            symbol,
            SignalAction.EXIT,
            open_side=open_side,  # type: ignore[arg-type]
            open_qty=open_qty,
            qty=share_qty,
            price=fill_price,
            reason=str(fixed_pct["reason"]),
            exit_qty=int(fixed_pct.get("exit_qty") or open_qty),
        )
        clear_atr_trail_state(symbol)
    elif profit_candle_action not in {"none", ""} and profit_candle.get("exit_qty"):
        logger.info("%s %s", symbol, profit_candle["reason"])
        intents = plan_equity_intents(
            symbol,
            SignalAction.EXIT,
            open_side=open_side,  # type: ignore[arg-type]
            open_qty=open_qty,
            qty=share_qty,
            price=fill_price,
            reason=str(profit_candle["reason"]),
            exit_qty=int(profit_candle.get("exit_qty") or open_qty),
        )
        clear_atr_trail_state(symbol)
    elif atr_mgmt.get("action") in atr_exit_actions:
        logger.info("%s %s", symbol, atr_mgmt["reason"])
        intents = plan_equity_intents(
            symbol,
            SignalAction.EXIT,
            open_side=open_side,  # type: ignore[arg-type]
            open_qty=open_qty,
            qty=share_qty,
            price=fill_price,
            reason=str(atr_mgmt["reason"]),
            exit_qty=int(atr_mgmt.get("exit_qty") or open_qty),
        )
        # Full flat clears trail memory; partial scale-out keeps extreme.
        if int(atr_mgmt.get("exit_qty") or 0) >= int(open_qty):
            clear_atr_trail_state(symbol)
    elif morning_tp.get("action") in {"profit_book", "stop"}:
        logger.info("%s %s", symbol, morning_tp["reason"])
        intents = plan_equity_intents(
            symbol,
            SignalAction.EXIT,
            open_side=open_side,  # type: ignore[arg-type]
            open_qty=open_qty,
            qty=share_qty,
            price=fill_price,
            reason=str(morning_tp["reason"]),
            exit_qty=int(morning_tp.get("exit_qty") or open_qty),
        )
        if int(morning_tp.get("exit_qty") or 0) >= int(open_qty):
            clear_atr_trail_state(symbol)
    elif opposite_pine_action not in {"none", ""} and opposite_pine.get("exit_qty"):
        # Close-only: EXIT intent (cooloff recorded like other closes).
        logger.info("%s %s", symbol, opposite_pine["reason"])
        intents = plan_equity_intents(
            symbol,
            SignalAction.EXIT,
            open_side=open_side,  # type: ignore[arg-type]
            open_qty=open_qty,
            qty=share_qty,
            price=fill_price,
            reason=str(opposite_pine["reason"]),
            exit_qty=int(opposite_pine.get("exit_qty") or open_qty),
        )
        clear_atr_trail_state(symbol)
    elif flatten["action"] == "flatten":
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
        clear_atr_trail_state(symbol)
    elif flatten["action"] == "overnight_hold":
        # Fixed-pct/ATR/morning did not exit — keep overnight conviction path.
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
    elif index_underlying:
        # Defense: never plan stock entry for indices even if earlier gate missed.
        intents = []
        result["note"] = "index_not_equity — entry refused"
    elif use_pine and decision.action == SignalAction.HOLD:
        # Pine HOLD = no trade (do not close open book — only sell_signal / exits).
        intents = []
        result["note"] = "pine_hold — no buy/sell signal"
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
        # Pine path: prefer close-only on opposite signal (no same-bar flip).
        # Primary path is opposite_pine_exit above; this strips flip_open if a
        # flip still reached the signal planner (e.g. policy disabled).
        if use_pine and any(
            getattr(i, "action_kind", None) == "flip_close" for i in intents
        ):
            intents = [
                i
                for i in intents
                if getattr(i, "action_kind", None) != "flip_open"
            ]
        # Fresh entry / flip_open should start without stale trail state.
        if any(getattr(i, "action_kind", None) in {"entry", "flip_open"} for i in intents):
            clear_atr_trail_state(symbol)

    # ₹20 floor on new entries even if scoring already ran (LTP may be cheaper
    # than last candle close). Exits / covers kept.
    floor_px = ltp_px if ltp_px is not None else (
        float(fill_price) if may_trade and fill_price else candle_close_early
    )
    if below_min_equity_price(floor_px, min_price=min_equity_price):
        # Always clear stale watchlist KV when mark is under the floor (even if
        # we already scored this tick from a candle close ≥ ₹20).
        try:
            from options_portal_persistence.kv_store import KvStore

            kv = KvStore()
            if kv.available and kv.delete(f"pine.signal.{symbol.upper()}"):
                result["pine_kv_cleared"] = True
                logger.info(
                    "%s cleared pine.signal KV (price_floor mark=%.2f)",
                    symbol.upper(),
                    float(floor_px),
                )
        except Exception:  # noqa: BLE001
            logger.exception("%s pine.signal KV clear failed", symbol)
        if any(
            getattr(i, "action_kind", None) in {"entry", "flip_open"} for i in intents
        ):
            floor_note = (
                f"price_floor — {float(floor_px):.2f} < ₹{min_equity_price:g} "
                "— new entry refused"
            )
            logger.info("%s %s", symbol, floor_note)
            before_floor = len(intents)
            intents = filter_entry_intents(intents, allow_entries=False)
            result["price_floor"] = True
            if before_floor and not intents:
                result["order"] = None
                result["note"] = floor_note
                return result
            result["note"] = f"{floor_note} — entry legs dropped; exits kept"

    # Cash entry window only: [entries_open_ist, no_new_entries_after) on trading days.
    # Nights / pre-09:30 / after cutoff → drop entry/flip_open; exits kept.
    # Soft flatten + overnight hold / ±2% risk exits use separate gates (unchanged).
    if not entry_session:
        before = len(intents)
        intents = filter_entry_intents(intents, allow_entries=False)
        if soft_policy.before_entries_open(ist_now):
            gate_note = (
                f"outside_entry_session: before entries_open_ist="
                f"{soft_policy.entries_open_ist} IST"
            )
        elif soft_policy.past_no_new_entries(ist_now):
            gate_note = (
                f"no_new_entries_after={soft_policy.no_new_entries_after} IST"
            )
        else:
            gate_note = "outside_entry_session"
        if before and not intents:
            logger.info("%s %s — blocked entry intents", symbol, gate_note)
            result["order"] = None
            result["note"] = gate_note
            return result
        if len(intents) < before:
            result["note"] = f"{gate_note} — entry legs dropped; exits kept"

    # Light index gate: bypassed on Pine path (Pine factors only; stocks-only still).
    if (
        (not use_pine)
        and mkt_policy.enabled
        and any(
            getattr(i, "action_kind", None) in {"entry", "flip_open"} for i in intents
        )
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
    elif use_pine:
        result["market_sentiment"] = {"bypassed": True, "reason": "pine_signals"}

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

    # High-conviction multi-factor gate — bypassed on Pine path (exact buy/sell only).
    # Re-entry cooloff still applies on Pine so manual_close / risk exits are not
    # immediately reversed by the next BUY/SELL tick.
    hc_enabled = bool(hc_cfg.get("enabled", True)) and (not use_pine)
    if use_pine:
        result["high_conviction"] = {
            "admitted": True,
            "reason": "bypassed_pine_signals",
            "floor": 0.0,
            "factors": [],
        }
        logger.info(
            "%s pine_entry_path — HC/MTF/RVOL/proximity/news/top10%% bypassed "
            "(reentry_cooloff still enforced)",
            symbol,
        )
        sel_cfg = entry_selection_config(cfg)
        cool = dict(sel_cfg.get("reentry_cooloff") or {})
        if (
            cool.get("enabled", True)
            and int(cool.get("minutes") or 0) > 0
            and any(
                getattr(i, "action_kind", None) == "entry"
                and (open_side is None or open_qty <= 0)
                for i in intents
            )
        ):
            last_exit = None
            try:
                if paper is not None and paper.available:
                    fills_cool = list(paper.list_all_fills(mode="dry_run"))
                    last_exit = last_exit_times_from_fills(fills_cool).get(
                        symbol.upper()
                    )
            except Exception:  # noqa: BLE001
                logger.exception("%s pine reentry_cooloff lookup failed", symbol)
            if in_reentry_cooloff(
                last_close=last_exit,
                now=ist_now,
                cooloff_minutes=int(cool.get("minutes") or 0),
                same_session_only=bool(cool.get("same_session_only", True)),
                session_day=pnl_ist_day(ist_now),
            ):
                mins = int(cool.get("minutes") or 0)
                cool_note = (
                    f"reentry_cooloff — {symbol.upper()} blocked {mins}m "
                    f"after exit (incl. manual_close)"
                )
                logger.info("%s %s", symbol, cool_note)
                intents = filter_entry_intents(intents, allow_entries=False)
                result["reentry_cooloff"] = True
                if not intents:
                    result["order"] = None
                    result["note"] = cool_note
                    return result
                result["note"] = cool_note
    if hc_enabled and any(
        getattr(i, "action_kind", None) == "entry"
        and (open_side is None or open_qty <= 0)
        for i in intents
    ):
        last_trade = None
        fills_for_gate: list[dict[str, Any]] = []
        try:
            if paper is not None and paper.available:
                fills_for_gate = list(paper.list_all_fills(mode="dry_run"))
                last_trade = last_trade_times_from_fills(fills_for_gate).get(
                    symbol.upper()
                )
        except Exception:  # noqa: BLE001
            logger.exception("%s cooloff lookup failed", symbol)
        adaptive_delta = 0.0
        try:
            adaptive_delta = load_adaptive_delta(cfg=cfg)
        except Exception:  # noqa: BLE001
            pass
        rolling_wr = None
        try:
            rolling_wr, _, _ = rolling_equity_win_rate(
                fills_for_gate,
                window_trips=int(hc_cfg.get("wr_window_trips", 20)),
            )
        except Exception:  # noqa: BLE001
            logger.exception("%s rolling WR lookup failed", symbol)
        gate = evaluate_entry_gate(
            symbol=symbol.upper(),
            hybrid_score=float(decision.hybrid_score),
            now=ist_now,
            candles_15m=candles,
            atr14=atr14,
            price=float(fill_price or candle_close or 0.0),
            news_items=news_items,
            sentiments=sentiments,
            last_trade=last_trade,
            is_index=index_underlying,
            in_entry_window=entry_session,
            adaptive_delta=adaptive_delta,
            rolling_wr=rolling_wr,
            scan_abs_scores=(
                scan_hybrid_abs_scores() if entry_admission is not None else None
            ),
            cfg=cfg,
        )
        result["high_conviction"] = {
            "admitted": gate.admitted,
            "reason": gate.reason,
            "floor": gate.effective_min_hybrid,
            "factors": [
                {"name": f.name, "passed": f.passed, "detail": f.detail}
                for f in gate.factors
            ],
        }
        skip_entry_log().record(gate)
        logger.info("%s", gate.log_line(symbol.upper(), ts=ist_now))
        if not gate.admitted:
            intents = filter_entry_intents(intents, allow_entries=False)
            if not intents:
                result["order"] = None
                result["note"] = f"high_conviction_skip: {gate.reason}"
                return result

    # Cycle admission: on Pine path execute entries immediately (no |hybrid| rank).
    # Legacy path: defer flat→new entries for |hybrid| ranking into free slots.
    # Keep exits / flip_close / flip_open immediate (risk + signal continuity).
    if entry_admission is not None and (not use_pine):
        deferred: list[Any] = []
        immediate: list[Any] = []
        for intent in intents:
            is_new_flat_entry = (
                intent.action_kind == "entry"
                and (open_side is None or open_qty <= 0)
            )
            if is_new_flat_entry:
                deferred.append(intent)
            else:
                immediate.append(intent)
        if deferred:
            from app.entry_admission import PendingEntry

            await entry_admission.stage(
                PendingEntry(
                    symbol=symbol.upper(),
                    hybrid_score=float(decision.hybrid_score),
                    confidence=float(decision.confidence),
                    intents=list(deferred),
                    safe_mode=str(safe_mode),
                    limits=dict(limits),
                    recommendation=(
                        f"{decision.action.value} |hybrid|="
                        f"{abs(decision.hybrid_score):.3f}"
                    ),
                )
            )
            result["entry_deferred"] = True
            result["deferred_entry_intents"] = len(deferred)
            logger.info(
                "%s entry deferred for |hybrid| ranking (|h|=%.3f)",
                symbol,
                abs(decision.hybrid_score),
            )
        intents = immediate
        if not intents:
            result["order"] = None
            result["note"] = "entry deferred for |hybrid| cycle ranking"
            return result

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
