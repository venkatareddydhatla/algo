"""Equity dry-run planner, risk gate, router, and paper ledger."""

from __future__ import annotations

from pathlib import Path

import pytest

from options_portal_domain.equity import plan_equity_intents
from options_portal_domain.types import OrderResult, SignalAction
from options_portal_persistence.paper_trades import PaperTradeStore
from options_portal_risk.gates import DefaultRiskGate
from options_portal_risk.order_router import SafeOrderRouter


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
        {"open_symbols": ["A", "B", "C", "D", "E"], "day_realized_pnl": 0},
        {"max_open_symbols": 5, "max_equity_qty_per_symbol": 1},
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
        {"open_symbols": ["A", "B", "C", "D", "E"], "day_realized_pnl": 0},
        {"max_open_symbols": 5, "max_equity_qty_per_symbol": 1},
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
        limits={"max_equity_qty_per_symbol": 1, "max_open_symbols": 5},
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
