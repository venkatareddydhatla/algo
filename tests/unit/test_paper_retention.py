"""Paper / dry-run trade retention: keep history, explicit purge, live ≥90d."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from options_portal_domain.equity import plan_equity_intents
from options_portal_domain.types import OrderResult, SignalAction
from options_portal_persistence.paper_trades import PaperTradeStore


@pytest.fixture()
def paper_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "paper.sqlite"
    monkeypatch.setenv("PERSISTENCE_PROFILE", "local")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    yield db


def _sim_fill(store: PaperTradeStore, symbol: str, client_id: str, price: float = 100.0):
    intent = plan_equity_intents(
        symbol,
        SignalAction.BUY_CE_BIAS,
        open_side=None,
        open_qty=0,
        qty=1,
        price=price,
        reason="retention-test",
    )[0]
    store.record_simulated_fill(
        intent,
        OrderResult(client_id, "simulated", "dry_run", f"SIM-{client_id}", None),
    )


def test_dry_run_keeps_fills_no_auto_purge(paper_env: Path):
    store = PaperTradeStore()
    _sim_fill(store, "RELIANCE", "r1")
    _sim_fill(store, "TCS", "t1", price=200.0)

    skipped = store.run_live_retention(mode="dry_run", min_retain_days=90)
    assert skipped["skipped"] is True
    assert skipped["purged"] == 0
    assert store.count_fills(mode="dry_run") == 2

    demo_skip = store.run_live_retention(mode="demo", min_retain_days=1)
    assert demo_skip["skipped"] is True
    assert store.count_fills(mode="dry_run") == 2


def test_archive_and_purge_requires_confirm(paper_env: Path):
    store = PaperTradeStore()
    _sim_fill(store, "RELIANCE", "r1")
    refused = store.archive_and_purge(mode="dry_run", confirm=False)
    assert refused["purged"] is False
    assert store.count_fills(mode="dry_run") == 1


def test_archive_and_purge_clears_ledger(paper_env: Path):
    store = PaperTradeStore()
    _sim_fill(store, "RELIANCE", "r1")
    assert store.get_position("RELIANCE") is not None

    result = store.archive_and_purge(
        mode="dry_run",
        confirm=True,
        archive=True,
        clear_positions=True,
    )
    assert result["purged"] is True
    assert result["fills_deleted"] == 1
    assert result["archived"] == 1
    assert result["positions_deleted"] == 1
    assert store.count_fills(mode="dry_run") == 0
    assert store.get_position("RELIANCE") is None

    # Archive table retained the copy
    with store._connect() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM paper_fills_archive").fetchone()["n"]
    assert int(n) == 1


def test_list_fills_paginates_full_history(paper_env: Path):
    store = PaperTradeStore()
    for i in range(5):
        _sim_fill(store, f"SYM{i}", f"c{i}", price=100.0 + i)

    page0 = store.list_fills(mode="dry_run", limit=2, offset=0)
    page1 = store.list_fills(mode="dry_run", limit=2, offset=2)
    assert len(page0) == 2
    assert len(page1) == 2
    assert {f["id"] for f in page0}.isdisjoint({f["id"] for f in page1})

    summary = store.summary(mode="dry_run", fills_limit=2, fills_offset=0)
    assert summary["fills_total"] == 5
    assert summary["fills_limit"] == 2
    assert summary["fills_offset"] == 0
    assert len(summary["fills"]) == 2
    assert summary["retention"]["auto_purge"] is False


def test_live_retention_keeps_at_least_90_days(paper_env: Path):
    store = PaperTradeStore()
    _sim_fill(store, "RELIANCE", "old1")
    # Backdate the fill far beyond 90 days
    old_ts = (datetime.now(tz=timezone.utc) - timedelta(days=120)).isoformat()
    with store._connect() as conn:
        conn.execute(
            "UPDATE paper_fills SET ts_utc = ?, mode = ? WHERE client_order_id = ?",
            (old_ts, "live", "old1"),
        )
        conn.commit()

    # Requesting a shorter window still floors at 90 days
    result = store.run_live_retention(
        mode="live",
        min_retain_days=30,
        as_of=datetime.now(tz=timezone.utc),
    )
    assert result["skipped"] is False
    assert result["min_retain_days"] == 90
    assert result["purged"] == 1
    assert store.count_fills(mode="live") == 0


@pytest.mark.asyncio
async def test_trades_api_pagination_and_admin_purge(paper_env: Path):
    store = PaperTradeStore()
    for i in range(3):
        _sim_fill(store, "RELIANCE", f"api-{i}", price=90.0 + i)

    from app.routes.trades import trades_summary

    page = await trades_summary(offset=0, limit=2)
    assert page["source"] == "paper_ledger"
    assert page["fills_total"] == 3
    assert len(page["fills"]) == 2
    assert page["retention"]["auto_purge"] is False

    from fastapi import HTTPException

    from app.routes.admin import PaperLedgerPurge, purge_paper_ledger

    with pytest.raises(HTTPException) as exc:
        await purge_paper_ledger(PaperLedgerPurge(confirm=False))
    assert exc.value.status_code == 400

    purged = await purge_paper_ledger(
        PaperLedgerPurge(confirm=True, archive=True, clear_positions=True, mode="dry_run")
    )
    assert purged["purged"] is True
    assert store.count_fills(mode="dry_run") == 0
