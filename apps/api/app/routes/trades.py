"""Trades / P&L read API (paper ledger mock)."""

from __future__ import annotations

from fastapi import APIRouter

from app.mock_data import MOCK_TRADES
from options_portal_domain.config import trading_mode

router = APIRouter(prefix="/api/v1", tags=["trades"])


@router.get("/trades")
async def trades_summary() -> dict:
    return {**MOCK_TRADES, "mode": trading_mode(), "source": "mock"}
