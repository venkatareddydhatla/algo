"""15m pipeline tick — skeleton orchestration."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from options_portal_broker.adapter import UpstoxAdapter
from options_portal_domain.calendar import IndiaMarketCalendar
from options_portal_domain.config import load_config, trading_mode
from options_portal_domain.types import SignalAction
from options_portal_hybrid.options_recommender import SingleLegATMRecommender
from options_portal_hybrid.scorer import WeightedHybridScorer
from options_portal_notify.notifiers import ConsoleNotifier
from options_portal_risk.order_router import SafeOrderRouter
from options_portal_sentiment.vader_scorer import VaderScorer
from options_portal_ta.indicators import EMAPlugin, RSIPlugin, SupertrendPlugin, VWAPPlugin
from options_portal_ta.registry import IndicatorRegistry
from options_portal_ta.signal_generator import ConfluenceSignalGenerator

logger = logging.getLogger(__name__)


def build_ta_registry() -> IndicatorRegistry:
    reg = IndicatorRegistry()
    reg.register(RSIPlugin(period=14))
    reg.register(EMAPlugin(fast=9, slow=21))
    reg.register(SupertrendPlugin())
    reg.register(VWAPPlugin())
    return reg


async def run_symbol_tick(symbol: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run one dry-run pipeline tick for a symbol (synthetic MD if no Upstox token)."""
    cfg = config or load_config()
    hybrid_cfg = cfg.get("hybrid", {})
    risk_cfg = cfg.get("trading", {}).get("risk", {})
    mode = trading_mode(cfg)  # type: ignore[arg-type]

    broker = UpstoxAdapter()
    calendar = IndiaMarketCalendar()
    registry = build_ta_registry()
    signal_gen = ConfluenceSignalGenerator()
    scorer = WeightedHybridScorer(
        weight_tech=float(hybrid_cfg.get("weights", {}).get("tech", 0.65)),
        weight_news=float(hybrid_cfg.get("weights", {}).get("news", 0.35)),
        enter_long=float(hybrid_cfg.get("enter_long", 0.45)),
        enter_short=float(hybrid_cfg.get("enter_short", -0.45)),
        news_veto_abs=float(hybrid_cfg.get("news_veto_abs", 0.70)),
    )
    recommender = SingleLegATMRecommender(min_oi=int(risk_cfg.get("min_option_oi", 50000)))
    router = SafeOrderRouter(broker=broker, halt=bool(cfg.get("trading", {}).get("halt")))
    notifier = ConsoleNotifier()
    sentiment = VaderScorer()

    end = datetime.now(tz=timezone.utc)
    start = end - timedelta(days=2)
    candles = await broker.get_candles(symbol, "15m", start, end)
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
    }

    if decision.action == SignalAction.HOLD:
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

    expiry = calendar.pick_expiry(symbol)
    chain = await broker.get_option_chain(symbol, expiry)
    reco = recommender.recommend(decision, chain, risk_cfg)
    if reco is None:
        result["order"] = None
        result["note"] = "no recommendation"
        return result

    # Force dry_run path for safety even if config says otherwise in worker skeleton
    safe_mode = "dry_run" if mode == "live" else mode
    order = await router.execute(reco, safe_mode, actor_user_id=None, limits={**risk_cfg, "halt": cfg.get("trading", {}).get("halt")})
    result["recommendation"] = reco.rationale
    result["order"] = {
        "status": order.status,
        "mode": order.mode,
        "client_order_id": order.client_order_id,
        "message": order.message,
    }
    await notifier.send(
        f"{symbol} {decision.action.value}",
        reco.rationale,
        {"order": result["order"]},
    )
    return result
