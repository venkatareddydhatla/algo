"""Local / SQLite persistence profile for Docker-free dry-run."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from options_portal_persistence.db import (
    check_postgres,
    check_redis,
    is_local_profile,
    persistence_profile,
)


@pytest.fixture()
def local_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "options_portal.local.sqlite"
    monkeypatch.setenv("PERSISTENCE_PROFILE", "local")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setenv("REDIS_URL", "memory://local")
    yield db


@pytest.mark.asyncio
async def test_local_profile_sqlite_and_redis_stub(local_env: Path):
    assert persistence_profile() == "local"
    assert is_local_profile() is True

    pg = await check_postgres()
    assert pg["status"] == "ok"
    assert pg["component"] == "sqlite"
    assert local_env.exists()

    rd = await check_redis()
    assert rd["status"] == "ok"
    assert "local stub" in rd.get("detail", "")


def test_sqlite_url_auto_selects_local(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PERSISTENCE_PROFILE", raising=False)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./data/x.sqlite")
    assert persistence_profile() == "local"


def test_postgres_default_when_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PERSISTENCE_PROFILE", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert persistence_profile() == "postgres"
    assert is_local_profile() is False
