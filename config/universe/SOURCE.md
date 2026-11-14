# NSE F&O underlyings snapshot

- **File:** `nse_fno_underlyings.csv` (indices + equity F&O symbols, lot sizes)
- **Source URL:** https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv
- **Alternate dated archive example:** https://nsearchives.nseindia.com/content/fo/fo_mktlots_31072025.csv
- **Snapshot date (UTC):** 2026-11-14
- **Counts at snapshot:** 6 index + 218 equity = 224 underlyings
- **Refresh:** `python scripts/refresh_nse_fno_universe.py` (use `--from-file` if NSE egress is blocked)

Lot sizes change with NSE circulars — verify before live sizing. Dry-run portal use does not place orders.
