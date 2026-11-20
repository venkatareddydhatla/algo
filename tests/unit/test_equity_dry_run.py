"""Equity dry-run planner, risk gate, router, and paper ledger."""

from __future__ import annotations

from pathlib import Path

import pytest

from options_portal_domain.equity import (
    below_min_equity_price,
    min_equity_price_from_config,
    plan_equity_intents,
    realized_equity_pnl,
    resolve_equity_mark,
    unrealized_equity_pnl,
)
from options_portal_domain.types import OrderResult, SignalAction
from options_portal_persistence.paper_trades import PaperTradeStore
from options_portal_risk.gates import DefaultRiskGate
from options_portal_risk.order_router import SafeOrderRouter


def test_min_equity_price_floor_defaults_to_20():
    """Symbols with LTP/close < ₹20 are excluded from score/entry."""
    from options_portal_domain.equity import filter_rows_below_min_equity_price

    assert min_equity_price_from_config({}) == pytest.approx(20.0)
    assert min_equity_price_from_config(
        {"trading": {"risk": {"min_equity_price": 20}}}
    ) == pytest.approx(20.0)
    assert min_equity_price_from_config(
        {"trading": {"risk": {"min_equity_price": 0}}}
    ) == pytest.approx(0.0)
    assert below_min_equity_price(19.99) is True
    assert below_min_equity_price(20.0) is False
    assert below_min_equity_price(20.01) is False
    assert below_min_equity_price(None) is False
    assert below_min_equity_price(15.0, min_price=0) is False

    kept, dropped = filter_rows_below_min_equity_price(
        [
            {"symbol": "IDEA", "price": 12.05, "action": "BUY"},
            {"symbol": "RELIANCE", "price": 1400.0, "action": "BUY"},
            {"symbol": "UNKNOWN", "action": "HOLD"},  # no mark → keep
        ],
        min_price=20.0,
    )
    assert [r["symbol"] for r in kept] == ["RELIANCE", "UNKNOWN"]
    assert [r["symbol"] for r in dropped] == ["IDEA"]


def test_resolve_equity_mark_prefers_ltp():
    mark = resolve_equity_mark(
        ltp=14350.25,
        candle_close=14346.45,
        entry_avg=14346.45,
    )
    assert mark.source == "ltp"
    assert mark.price == pytest.approx(14350.25)


def test_resolve_equity_mark_falls_back_to_candle_then_entry():
    candle = resolve_equity_mark(
        ltp=None,
        candle_close=14340.0,
        entry_avg=14346.45,
    )
    assert candle.source == "candle_close"
    assert candle.price == pytest.approx(14340.0)
    assert "LTP unavailable" in candle.detail

    entry = resolve_equity_mark(ltp=None, candle_close=None, entry_avg=14346.45)
    assert entry.source == "entry_avg"
    assert entry.price == pytest.approx(14346.45)
    assert "PnL will be 0" in entry.detail


def test_realized_and_unrealized_pnl_math():
    assert realized_equity_pnl("long", 100.0, 110.0, 2) == pytest.approx(20.0)
    assert realized_equity_pnl("short", 200.0, 180.0, 1) == pytest.approx(20.0)
    assert realized_equity_pnl("long", 14346.45, 14346.45, 1) == pytest.approx(0.0)
    assert unrealized_equity_pnl("long", 100.0, 105.0, 3) == pytest.approx(15.0)
    assert unrealized_equity_pnl("short", 100.0, 90.0, 2) == pytest.approx(20.0)


def test_exit_intent_uses_provided_mark_not_entry():
    """soft_flatten / signal exit must carry live mark into limit_price."""
    exit_i = plan_equity_intents(
        "MIDCPNIFTY",
        SignalAction.HOLD,
        open_side="long",
        open_qty=1,
        qty=1,
        price=14355.10,  # live mark at close — not entry 14346.45
        reason="soft_flatten_ist=15:20: close long",
    )[0]
    assert exit_i.action_kind == "exit"
    assert exit_i.limit_price == pytest.approx(14355.10)
    assert exit_i.limit_price != pytest.approx(14346.45)


def test_plan_ce_bias_buys_long():
    intents = plan_equity_intents(
        "RELIANCE",
        SignalAction.BUY_CE_BIAS,
        open_side=None,
        open_qty=0,
        qty=1,
        price=2500.0,
        reason="hybrid bullish",
    )
    assert len(intents) == 1
    assert intents[0].side == "BUY"
    assert intents[0].action_kind == "entry"
    assert intents[0].resulting_position == "long"


def test_plan_pine_buy_sell_actions():
    buy = plan_equity_intents(
        "RELIANCE",
        SignalAction.BUY,
        open_side=None,
        open_qty=0,
        qty=1,
        price=2500.0,
        reason="pine buy_signal",
    )
    assert len(buy) == 1
    assert buy[0].side == "BUY"
    assert buy[0].resulting_position == "long"
    sell = plan_equity_intents(
        "TCS",
        SignalAction.SELL,
        open_side=None,
        open_qty=0,
        qty=1,
        price=3900.0,
        reason="pine sell_signal",
    )
    assert len(sell) == 1
    assert sell[0].side == "SELL"
    assert sell[0].resulting_position == "short"


def test_plan_pe_bias_sells_short():
    intents = plan_equity_intents(
        "TCS",
        SignalAction.BUY_PE_BIAS,
        open_side=None,
        open_qty=0,
        qty=2,
        price=3900.0,
    )
    assert len(intents) == 1
    assert intents[0].side == "SELL"
    assert intents[0].qty == 2
    assert intents[0].resulting_position == "short"


def test_plan_hold_closes_long():
    intents = plan_equity_intents(
        "RELIANCE",
        SignalAction.HOLD,
        open_side="long",
        open_qty=1,
        qty=1,
        price=2510.0,
    )
    assert len(intents) == 1
    assert intents[0].side == "SELL"
    assert intents[0].action_kind == "exit"
    assert intents[0].resulting_position is None


def test_plan_hold_covers_short():
    intents = plan_equity_intents(
        "TCS",
        "HOLD",
        open_side="short",
        open_qty=1,
        qty=1,
        price=3850.0,
    )
    assert len(intents) == 1
    assert intents[0].side == "BUY"
    assert intents[0].action_kind == "exit"


def test_plan_hold_noop_without_position():
    assert (
        plan_equity_intents(
            "RELIANCE",
            SignalAction.HOLD,
            open_side=None,
            open_qty=0,
            qty=1,
            price=100.0,
        )
        == []
    )


def test_plan_no_duplicate_entry():
    assert (
        plan_equity_intents(
            "RELIANCE",
            SignalAction.BUY_CE_BIAS,
            open_side="long",
            open_qty=1,
            qty=1,
            price=100.0,
        )
        == []
    )


def test_plan_flip_long_to_short():
    intents = plan_equity_intents(
        "RELIANCE",
        SignalAction.BUY_PE_BIAS,
        open_side="long",
        open_qty=1,
        qty=1,
        price=100.0,
    )
    assert len(intents) == 2
    assert intents[0].action_kind == "flip_close"
    assert intents[0].side == "SELL"
    assert intents[1].action_kind == "flip_open"
    assert intents[1].side == "SELL"
    assert intents[1].resulting_position == "short"


def test_risk_gate_blocks_max_open_on_equity_entry():
    intent = plan_equity_intents(
        "INFY",
        SignalAction.BUY_CE_BIAS,
        open_side=None,
        open_qty=0,
        qty=1,
        price=1500.0,
    )[0]
    ok, reason = DefaultRiskGate().validate_equity(
        intent,
        {
            "open_symbols": ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"],
            "day_realized_pnl": 0,
        },
        {"max_open_symbols": 10, "max_equity_qty_per_symbol": 1},
    )
    assert ok is False
    assert "Max open symbols" in reason


def test_risk_gate_allows_exit_when_open_full():
    intent = plan_equity_intents(
        "A",
        SignalAction.HOLD,
        open_side="long",
        open_qty=1,
        qty=1,
        price=10.0,
    )[0]
    ok, _ = DefaultRiskGate().validate_equity(
        intent,
        {
            "open_symbols": ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"],
            "day_realized_pnl": 0,
        },
        {"max_open_symbols": 10, "max_equity_qty_per_symbol": 1},
    )
    assert ok is True


def test_risk_gate_day_loss_blocks_entry():
    intent = plan_equity_intents(
        "INFY",
        SignalAction.BUY_CE_BIAS,
        open_side=None,
        open_qty=0,
        qty=1,
        price=1500.0,
    )[0]
    ok, reason = DefaultRiskGate().validate_equity(
        intent,
        {"open_symbols": [], "day_realized_pnl": -30000},
        {"max_day_loss_inr": 25000, "max_equity_qty_per_symbol": 1},
    )
    assert ok is False
    assert "Day loss" in reason


@pytest.mark.asyncio
async def test_equity_router_dry_run_simulates():
    intent = plan_equity_intents(
        "RELIANCE",
        SignalAction.BUY_CE_BIAS,
        open_side=None,
        open_qty=0,
        qty=1,
        price=2500.0,
    )[0]
    result = await SafeOrderRouter().execute_equity(
        intent,
        "dry_run",
        limits={"max_equity_qty_per_symbol": 1, "max_open_symbols": 10},
    )
    assert result.status == "simulated"
    assert result.mode == "dry_run"
    assert "equity" in (result.message or "").lower() or "BUY" in (result.message or "")


@pytest.mark.asyncio
async def test_equity_router_blocks_live():
    intent = plan_equity_intents(
        "RELIANCE",
        SignalAction.BUY_PE_BIAS,
        open_side=None,
        open_qty=0,
        qty=1,
        price=2500.0,
    )[0]
    result = await SafeOrderRouter(v1_block_live=True).execute_equity(intent, "live")
    assert result.status == "rejected"
    assert "live" in (result.message or "").lower()


@pytest.fixture()
def paper_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "paper.sqlite"
    monkeypatch.setenv("PERSISTENCE_PROFILE", "local")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    yield db


def test_paper_ledger_long_then_exit(paper_env: Path):
    store = PaperTradeStore()
    assert store.available
    entry = plan_equity_intents(
        "RELIANCE",
        SignalAction.BUY_CE_BIAS,
        open_side=None,
        open_qty=0,
        qty=1,
        price=100.0,
        reason="entry",
    )[0]
    order = OrderResult(
        client_order_id="eq-dry-1",
        status="simulated",
        mode="dry_run",
        broker_order_id="SIM-eq-dry-1",
        message="ok",
    )
    meta = store.record_simulated_fill(entry, order)
    assert meta["persisted"] is True
    pos = store.get_position("RELIANCE")
    assert pos is not None
    assert pos["side"] == "long"
    assert int(pos["qty"]) == 1

    exit_i = plan_equity_intents(
        "RELIANCE",
        SignalAction.HOLD,
        open_side="long",
        open_qty=1,
        qty=1,
        price=110.0,
        reason="exit",
    )[0]
    order2 = OrderResult(
        client_order_id="eq-dry-2",
        status="simulated",
        mode="dry_run",
        broker_order_id="SIM-eq-dry-2",
        message="ok",
    )
    meta2 = store.record_simulated_fill(exit_i, order2)
    assert meta2["realized_pnl"] == pytest.approx(10.0)
    assert store.get_position("RELIANCE") is None

    # Concurrent soft-flatten / scan must not invent a second exit fill/email.
    dup = store.record_simulated_fill(
        exit_i,
        OrderResult(
            client_order_id="eq-dry-2b",
            status="simulated",
            mode="dry_run",
            broker_order_id="SIM-eq-dry-2b",
            message="ok",
        ),
    )
    assert dup["persisted"] is False
    assert "no open position" in str(dup.get("reason") or "")

    summary = store.summary()
    assert summary["instrument"] == "equity"
    assert summary["dry_run"] is True
    assert summary["fills_kind"] == "round_trip"
    assert summary["raw_fills_total"] == 2
    assert len(summary["fills"]) == 1
    assert len(summary["closed_trades"]) == 1
    trade = summary["fills"][0]
    assert trade["dry_run"] is True
    assert trade["side"] == "LONG"
    assert trade["symbol"] == "RELIANCE"
    assert trade["entry_ts_ist_display"].endswith("IST")
    assert trade["exit_ts_ist_display"].endswith("IST")
    assert trade["entry_price"] == pytest.approx(100.0)
    assert trade["exit_price"] == pytest.approx(110.0)
    assert trade["pnl"] == pytest.approx(10.0)
    assert "reason" in trade


def test_paper_ledger_short_cover_pnl(paper_env: Path):
    store = PaperTradeStore()
    entry = plan_equity_intents(
        "TCS",
        SignalAction.BUY_PE_BIAS,
        open_side=None,
        open_qty=0,
        qty=1,
        price=200.0,
    )[0]
    store.record_simulated_fill(
        entry,
        OrderResult("c1", "simulated", "dry_run", "SIM-c1", None),
    )
    cover = plan_equity_intents(
        "TCS",
        SignalAction.HOLD,
        open_side="short",
        open_qty=1,
        qty=1,
        price=180.0,
    )[0]
    meta = store.record_simulated_fill(
        cover,
        OrderResult("c2", "simulated", "dry_run", "SIM-c2", None),
    )
    assert meta["realized_pnl"] == pytest.approx(20.0)


def test_paper_ledger_exit_mark_pnl_and_upnl_marks(paper_env: Path):
    """Exit fill stores mark ≠ entry; summary uPnL uses live marks map."""
    store = PaperTradeStore()
    entry = plan_equity_intents(
        "MIDCPNIFTY",
        SignalAction.BUY_CE_BIAS,
        open_side=None,
        open_qty=0,
        qty=1,
        price=14346.45,
        reason="entry",
    )[0]
    store.record_simulated_fill(
        entry,
        OrderResult("m1", "simulated", "dry_run", "SIM-m1", None),
    )
    # Open uPnL with live mark
    snap = store.summary(marks={"MIDCPNIFTY": 14360.0})
    assert len(snap["positions"]) == 1
    assert snap["positions"][0]["mark_source"] == "ltp"
    assert snap["positions"][0]["mark_price"] == pytest.approx(14360.0)
    assert snap["positions"][0]["unrealized_pnl"] == pytest.approx(13.55)
    assert snap["day_unrealized"] == pytest.approx(13.55)

    exit_i = plan_equity_intents(
        "MIDCPNIFTY",
        SignalAction.HOLD,
        open_side="long",
        open_qty=1,
        qty=1,
        price=14360.0,
        reason="soft_flatten",
    )[0]
    meta = store.record_simulated_fill(
        exit_i,
        OrderResult("m2", "simulated", "dry_run", "SIM-m2", None),
    )
    assert meta["realized_pnl"] == pytest.approx(13.55)
    closed = store.summary()["closed_trades"][0]
    assert closed["entry_price"] == pytest.approx(14346.45)
    assert closed["exit_price"] == pytest.approx(14360.0)
    assert closed["pnl"] == pytest.approx(13.55)


def test_closing_n_symbols_do_not_share_one_exit_price(paper_env: Path):
    """Regression: soft-close N names with distinct LTP map → distinct Exit px.

    Bug: every closed trade showed Exit px ₹996.96 (360ONE's entry) because LTP
    selection took the first quote / synthetic base-1000 close for all symbols.
    """
    store = PaperTradeStore()
    # Distinct per-symbol entries (must not be overwritten by a shared mark)
    book = {
        "360ONE": {"entry": 1176.80, "ltp": 1180.50},
        "IOC": {"entry": 144.04, "ltp": 145.20},
        "BPCL": {"entry": 340.10, "ltp": 348.75},
        "AXISBANK": {"entry": 1120.00, "ltp": 1111.25},
        "ASIANPAINT": {"entry": 2910.00, "ltp": 2895.40},
        "APOLLOHOSP": {"entry": 7005.00, "ltp": 7050.10},
        "ADANIPORTS": {"entry": 1210.50, "ltp": 1222.00},
    }
    for sym, px in book.items():
        entry = plan_equity_intents(
            sym,
            SignalAction.BUY_CE_BIAS,
            open_side=None,
            open_qty=0,
            qty=1,
            price=px["entry"],
            reason=f"{sym} entry",
        )[0]
        store.record_simulated_fill(
            entry,
            OrderResult(f"e-{sym}", "simulated", "dry_run", f"SIM-e-{sym}", None),
        )
        pos = store.get_position(sym)
        assert pos is not None
        assert float(pos["avg_price"]) == pytest.approx(px["entry"])

    # Soft-close each symbol at THAT symbol's LTP (not a shared mark)
    for sym, px in book.items():
        mark = resolve_equity_mark(ltp=px["ltp"], candle_close=None, entry_avg=px["entry"])
        assert mark.source == "ltp"
        assert mark.price == pytest.approx(px["ltp"])
        exit_i = plan_equity_intents(
            sym,
            SignalAction.HOLD,
            open_side="long",
            open_qty=1,
            qty=1,
            price=mark.price,
            reason="soft_flatten",
        )[0]
        assert exit_i.limit_price == pytest.approx(px["ltp"])
        store.record_simulated_fill(
            exit_i,
            OrderResult(f"x-{sym}", "simulated", "dry_run", f"SIM-x-{sym}", None),
        )

    closed = {t["symbol"]: t for t in store.summary()["closed_trades"]}
    assert set(closed) == set(book)
    exit_prices = [closed[s]["exit_price"] for s in book]
    entry_prices = [closed[s]["entry_price"] for s in book]
    assert len(set(exit_prices)) == len(book), exit_prices
    assert len(set(entry_prices)) == len(book), entry_prices
    # Classic contamination print must not appear on any name's entry or exit
    assert 996.96 not in exit_prices
    assert 996.96 not in entry_prices
    for sym, px in book.items():
        assert closed[sym]["entry_price"] == pytest.approx(px["entry"])
        assert closed[sym]["exit_price"] == pytest.approx(px["ltp"])
        assert closed[sym]["exit_price"] != pytest.approx(996.96)
        assert closed[sym]["entry_price"] != pytest.approx(996.96)
    # IOC must not inherit 360ONE marks
    assert closed["IOC"]["entry_price"] == pytest.approx(144.04)
    assert closed["IOC"]["exit_price"] == pytest.approx(145.20)
    assert closed["360ONE"]["exit_price"] == pytest.approx(1180.50)


def test_ioc_and_360one_round_trips_keep_own_entry_and_exit(paper_env: Path):
    """User bug: IOC SHORT entry 996.96 (360ONE) / exit synthetic — marks must be per-symbol."""
    store = PaperTradeStore()
    # SHORT entries (PE bias)
    for sym, entry, exit_px in (
        ("IOC", 144.04, 145.50),
        ("360ONE", 1176.80, 1160.25),
        ("BPCL", 302.00, 305.10),
    ):
        open_i = plan_equity_intents(
            sym,
            SignalAction.BUY_PE_BIAS,
            open_side=None,
            open_qty=0,
            qty=1,
            price=entry,
            reason=f"{sym} short entry",
        )[0]
        assert open_i.side == "SELL"
        store.record_simulated_fill(
            open_i,
            OrderResult(f"e-{sym}", "simulated", "dry_run", f"SIM-e-{sym}", None),
        )
        mark = resolve_equity_mark(ltp=exit_px, candle_close=None, entry_avg=entry)
        assert mark.source == "ltp"
        assert mark.price == pytest.approx(exit_px)
        # Cover short
        cover = plan_equity_intents(
            sym,
            SignalAction.HOLD,
            open_side="short",
            open_qty=1,
            qty=1,
            price=mark.price,
            reason="soft_flatten",
        )[0]
        assert cover.side == "BUY"
        store.record_simulated_fill(
            cover,
            OrderResult(f"x-{sym}", "simulated", "dry_run", f"SIM-x-{sym}", None),
        )

    closed = {t["symbol"]: t for t in store.summary()["closed_trades"]}
    assert closed["IOC"]["entry_price"] == pytest.approx(144.04)
    assert closed["IOC"]["exit_price"] == pytest.approx(145.50)
    assert closed["360ONE"]["entry_price"] == pytest.approx(1176.80)
    assert closed["360ONE"]["exit_price"] == pytest.approx(1160.25)
    # Contamination prints
    assert closed["IOC"]["entry_price"] != pytest.approx(996.96)
    assert closed["IOC"]["exit_price"] != pytest.approx(996.96)
    assert closed["IOC"]["exit_price"] != pytest.approx(3958.95)  # old IOC synthetic
    assert closed["IOC"]["entry_price"] != closed["360ONE"]["entry_price"]
    assert closed["IOC"]["exit_price"] != closed["360ONE"]["exit_price"]


@pytest.mark.asyncio
async def test_trades_api_reads_paper_ledger(paper_env: Path):
    store = PaperTradeStore()
    intent = plan_equity_intents(
        "RELIANCE",
        SignalAction.BUY_CE_BIAS,
        open_side=None,
        open_qty=0,
        qty=1,
        price=99.0,
        reason="api test",
    )[0]
    store.record_simulated_fill(
        intent,
        OrderResult("api-1", "simulated", "dry_run", "SIM-api-1", None),
    )

    from app.routes.trades import trades_summary

    payload = await trades_summary()
    assert payload["source"] == "paper_ledger"
    assert payload["instrument"] == "equity"
    assert payload["dry_run"] is True
    assert payload["fills_kind"] == "round_trip"
    # Entry-only: open leg under positions, not closed trades
    assert any(p["symbol"] == "RELIANCE" for p in payload["positions"])
    assert payload["fills_total"] == 0
    assert payload["raw_fills_total"] >= 1
    assert payload["closed_trades"] == []


@pytest.mark.asyncio
async def test_manual_close_long_sell(paper_env: Path, monkeypatch: pytest.MonkeyPatch):
    """POST close: long → SELL, reason manual_close, position cleared."""
    monkeypatch.setenv("TRADING_MODE", "dry_run")
    store = PaperTradeStore()
    entry = plan_equity_intents(
        "RELIANCE",
        SignalAction.BUY,
        open_side=None,
        open_qty=0,
        qty=1,
        price=100.0,
        reason="entry",
    )[0]
    store.record_simulated_fill(
        entry,
        OrderResult("mc-e1", "simulated", "dry_run", "SIM-mc-e1", None),
    )
    assert store.get_position("RELIANCE") is not None

    async def _marks(symbols: list[str]):
        return ({"RELIANCE": 112.0}, {"RELIANCE": "ltp"})

    monkeypatch.setattr(
        "app.routes.trades._open_position_marks",
        _marks,
    )

    from app.routes.trades import ManualCloseBody, close_open_position

    out = await close_open_position("reliance", ManualCloseBody())
    assert out["ok"] is True
    assert out["symbol"] == "RELIANCE"
    assert out["order_side"] == "SELL"
    assert out["closed_side"] == "long"
    assert out["reason"] == "manual_close"
    assert out["dry_run"] is True
    assert out["price"] == pytest.approx(112.0)
    assert out["realized_pnl"] == pytest.approx(12.0)
    assert store.get_position("RELIANCE") is None

    summary = store.summary()
    assert summary["closed_trades_total"] == 1
    trade = summary["closed_trades"][0]
    assert "manual_close" in str(trade.get("reason") or "")


@pytest.mark.asyncio
async def test_manual_close_short_cover(paper_env: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRADING_MODE", "dry_run")
    store = PaperTradeStore()
    entry = plan_equity_intents(
        "TCS",
        SignalAction.SELL,
        open_side=None,
        open_qty=0,
        qty=1,
        price=200.0,
        reason="short entry",
    )[0]
    store.record_simulated_fill(
        entry,
        OrderResult("mc-s1", "simulated", "dry_run", "SIM-mc-s1", None),
    )

    async def _marks(symbols: list[str]):
        return ({"TCS": 185.0}, {"TCS": "ltp"})

    monkeypatch.setattr("app.routes.trades._open_position_marks", _marks)

    from app.routes.trades import ManualCloseBody, close_open_position

    out = await close_open_position("TCS", ManualCloseBody(reason="manual_close"))
    assert out["order_side"] == "BUY"
    assert out["closed_side"] == "short"
    assert out["realized_pnl"] == pytest.approx(15.0)
    assert store.get_position("TCS") is None


@pytest.mark.asyncio
async def test_manual_close_missing_position(paper_env: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRADING_MODE", "dry_run")
    from fastapi import HTTPException

    from app.routes.trades import ManualCloseBody, close_open_position

    with pytest.raises(HTTPException) as exc:
        await close_open_position("NOSUCH", ManualCloseBody())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_manual_close_falls_back_when_marks_timeout(
    paper_env: Path, monkeypatch: pytest.MonkeyPatch
):
    """Slow MD must not block SQLite close — use entry avg."""
    import asyncio

    monkeypatch.setenv("TRADING_MODE", "dry_run")
    store = PaperTradeStore()
    entry = plan_equity_intents(
        "HINDALCO",
        SignalAction.BUY,
        open_side=None,
        open_qty=0,
        qty=1,
        price=700.0,
        reason="entry",
    )[0]
    store.record_simulated_fill(
        entry,
        OrderResult("h-e1", "simulated", "dry_run", "SIM-h-e1", None),
    )

    async def _hang(symbols: list[str]):
        await asyncio.sleep(30)
        return ({}, {})

    monkeypatch.setattr("app.routes.trades._open_position_marks", _hang)
    monkeypatch.setattr("app.routes.trades._CLOSE_MARK_TIMEOUT_SEC", 0.05)

    from app.routes.trades import ManualCloseBody, close_open_position

    out = await close_open_position("HINDALCO", ManualCloseBody(confirm=True))
    assert out["ok"] is True
    assert out["symbol"] == "HINDALCO"
    assert out["order_side"] == "SELL"
    assert out["mark_source"] == "entry_avg"
    assert out["price"] == pytest.approx(700.0)
    assert store.get_position("HINDALCO") is None
