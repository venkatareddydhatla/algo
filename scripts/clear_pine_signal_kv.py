#!/usr/bin/env python3
"""One-shot: delete pine.signal.* KV rows so GET /signals rebuilds from candles.

Native Windows (API/worker stopped or running) — must load C:\\algo\\.env so
KvStore opens the same SQLite file as start-native (PERSISTENCE_PROFILE=local).

Usage (from C:\\algo):
  .\\.venv\\Scripts\\python.exe scripts\\clear_pine_signal_kv.py
  .\\.venv\\Scripts\\python.exe scripts\\clear_pine_signal_kv.py --confirm
  .\\.venv\\Scripts\\python.exe scripts\\clear_pine_signal_kv.py --symbol BAJFINANCE --confirm
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for rel in (
    "packages/domain/src",
    "packages/persistence/src",
    "apps/api",
):
    p = ROOT / rel
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def _load_dotenv(path: Path, *, force: bool = False) -> int:
    """Minimal .env loader (no python-dotenv). Returns how many keys applied."""
    if not path.is_file():
        return 0
    applied = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        eq = line.find("=")
        if eq < 1:
            continue
        key = line[:eq].strip()
        val = line[eq + 1 :].strip()
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            val = val[1:-1]
        if force or key not in os.environ:
            os.environ[key] = val
            applied += 1
    return applied


def _bootstrap_native_sqlite(env_file: Path) -> Path:
    """Match start-native.ps1: local profile + absolute sqlite under repo data/.

    Without this, bare ``python scripts/clear_pine_signal_kv.py`` defaults to
    postgres profile → ``KvStore unavailable`` while API/worker use
    ``data/options_portal.local.sqlite``.
    """
    loaded = _load_dotenv(env_file, force=False)
    # Always prefer local for this maintenance script (native dry-run).
    os.environ["PERSISTENCE_PROFILE"] = (
        os.environ.get("PERSISTENCE_PROFILE") or "local"
    ).strip() or "local"
    if os.environ["PERSISTENCE_PROFILE"].lower() == "sqlite":
        os.environ["PERSISTENCE_PROFILE"] = "local"

    data_dir = ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    sqlite_file = data_dir / "options_portal.local.sqlite"
    # Absolute path so CWD does not matter (same file as start-native rewrite).
    abs_url = "sqlite:///" + sqlite_file.resolve().as_posix()

    raw_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not raw_url or raw_url.lower().startswith("sqlite"):
        # Relative ./data/... or missing → pin to repo data file.
        if (
            not raw_url
            or "/data/" in raw_url.replace("\\", "/")
            or raw_url.endswith("options_portal.local.sqlite")
            or raw_url.startswith("sqlite:///./")
        ):
            os.environ["DATABASE_URL"] = abs_url
        elif raw_url.lower().startswith("sqlite:///"):
            # Keep explicit absolute sqlite URL from .env when not the default relative.
            pass
        else:
            os.environ["DATABASE_URL"] = abs_url
    else:
        # Non-sqlite URL with PERSISTENCE_PROFILE=local — still force local file.
        os.environ["DATABASE_URL"] = abs_url

    print(
        f"env_file={env_file} loaded_keys={loaded} "
        f"PERSISTENCE_PROFILE={os.environ.get('PERSISTENCE_PROFILE')} "
        f"DATABASE_URL={os.environ.get('DATABASE_URL')}"
    )
    return sqlite_file.resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--symbol",
        action="append",
        default=[],
        help="Symbol to clear (repeatable). Default: full watchlist.",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required to delete keys (dry-run prints targets otherwise).",
    )
    parser.add_argument(
        "--env-file",
        default=str(ROOT / ".env"),
        help="Path to .env (default: repo root .env)",
    )
    args = parser.parse_args()

    # Chdir to repo root so relative config paths resolve like native start.
    os.chdir(ROOT)
    sqlite_path = _bootstrap_native_sqlite(Path(args.env_file))

    from options_portal_domain.config import load_config, watchlist_symbols
    from options_portal_persistence.db import database_url, is_local_profile
    from options_portal_persistence.kv_store import KvStore

    if not is_local_profile():
        print(
            "ERROR: PERSISTENCE_PROFILE is not local after bootstrap — "
            f"profile check failed (url={database_url()})",
            file=sys.stderr,
        )
        return 1

    cfg = load_config()
    symbols = [s.upper() for s in args.symbol] or [
        str(s).upper() for s in watchlist_symbols(cfg)
    ]
    kv = KvStore()
    if not kv.available:
        print(
            "KvStore unavailable — nothing to clear\n"
            f"  expected_sqlite={sqlite_path} exists={sqlite_path.is_file()}\n"
            f"  DATABASE_URL={database_url()}\n"
            "  Hint: ensure C:\\algo\\.env has PERSISTENCE_PROFILE=local and "
            "DATABASE_URL=sqlite:///./data/options_portal.local.sqlite "
            "(or re-run after git pull of the clear-script fix).",
            file=sys.stderr,
        )
        return 1

    print(f"sqlite={sqlite_path} exists={sqlite_path.is_file()} targets={len(symbols)} confirm={args.confirm}")
    cleared: list[str] = []
    present = 0
    for sym in symbols:
        key = f"pine.signal.{sym}"
        payload = kv.get(key)
        has = isinstance(payload, dict) and payload.get("action")
        if has:
            present += 1
        if not args.confirm:
            if has:
                print(
                    f"would clear {key} action={payload.get('action')} "
                    f"trend={payload.get('trend')} "
                    f"candle_source={payload.get('candle_source') or (payload.get('features') or {}).get('candle_source')}"
                )
            continue
        if kv.delete(key):
            if has:
                cleared.append(sym)
                print(f"cleared {key}")
            else:
                # delete returns True even when absent — only count prior hits
                pass
    if not args.confirm:
        print(f"present={present} (dry-run). Re-run with --confirm to delete.")
        return 0
    print(f"cleared {len(cleared)} keys (had action) present_before={present}")
    if present == 0:
        print(
            "Note: no pine.signal.* rows had an action — store is open but empty "
            "for the watchlist (already clear, or worker never wrote Upstox Pine)."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
