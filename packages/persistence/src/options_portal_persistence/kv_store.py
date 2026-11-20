"""Simple SQLite key/value store for adaptive overrides (host.kv-style).

Used by high-conviction adaptive |hybrid| feedback. No secrets — numeric /
JSON config overrides only.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from options_portal_persistence.db import _sqlite_path_from_url, database_url, is_local_profile
from options_portal_persistence.sqlite_schema import apply_kv_schema

logger = logging.getLogger(__name__)


class KvStore:
    """Local SQLite KV. Unavailable when not on local/sqlite profile."""

    def __init__(self, url: str | None = None) -> None:
        self._url = url or database_url()
        self._path: Path | None = None
        self.available = False
        if is_local_profile() or self._url.lower().startswith("sqlite"):
            self._path = _sqlite_path_from_url(self._url)
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._connect() as conn:
                    apply_kv_schema(conn)
                self.available = True
            except Exception:  # noqa: BLE001
                logger.exception("kv_store init failed")
                self.available = False

    def _connect(self) -> sqlite3.Connection:
        assert self._path is not None
        if str(self._path) == ":memory:":
            conn = sqlite3.connect(":memory:", check_same_thread=False)
        else:
            conn = sqlite3.connect(str(self._path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def get(self, key: str, default: Any = None) -> Any:
        if not self.available:
            return default
        try:
            with self._connect() as conn:
                apply_kv_schema(conn)
                row = conn.execute(
                    "SELECT value FROM kv_store WHERE key = ?", (str(key),)
                ).fetchone()
            if row is None:
                return default
            return json.loads(row["value"])
        except Exception:  # noqa: BLE001
            logger.exception("kv_store get failed key=%s", key)
            return default

    def set(self, key: str, value: Any) -> bool:
        if not self.available:
            return False
        try:
            payload = json.dumps(value)
            stamp = datetime.now(tz=timezone.utc).isoformat()
            with self._connect() as conn:
                apply_kv_schema(conn)
                conn.execute(
                    """
                    INSERT INTO kv_store (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                      value = excluded.value,
                      updated_at = excluded.updated_at
                    """,
                    (str(key), payload, stamp),
                )
                conn.commit()
            return True
        except Exception:  # noqa: BLE001
            logger.exception("kv_store set failed key=%s", key)
            return False

    def delete(self, key: str) -> bool:
        """Remove a key. Returns True when a row was deleted or key was absent."""
        if not self.available:
            return False
        try:
            with self._connect() as conn:
                apply_kv_schema(conn)
                conn.execute("DELETE FROM kv_store WHERE key = ?", (str(key),))
                conn.commit()
            return True
        except Exception:  # noqa: BLE001
            logger.exception("kv_store delete failed key=%s", key)
            return False
