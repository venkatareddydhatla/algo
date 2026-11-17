# NSE F&O underlyings snapshot

- **File:** `nse_fno_underlyings.csv` (indices + equity F&O symbols, lot sizes)
- **Source URL:** https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv
- **Alternate dated archive example:** https://nsearchives.nseindia.com/content/fo/fo_mktlots_31072025.csv
- **Snapshot date (UTC):** 2026-11-14
- **Counts at snapshot:** 6 index + 218 equity = 224 underlyings
- **Refresh:** `python scripts/refresh_nse_fno_universe.py` (use `--from-file` if NSE egress is blocked)

Lot sizes change with NSE circulars — verify before live sizing. Dry-run portal use does not place orders.

## Equity instrument keys (Upstox LTP / candles)

- **File:** `nse_eq_instrument_keys.csv` (`symbol,instrument_key,name`)
- **Source:** Upstox NSE instruments master  
  `https://assets.upstox.com/market-quote/instruments/exchange/NSE.csv.gz`
- **Purpose:** Map every F&O equity (IOC, 360ONE, BPCL, …) to `NSE_EQ|<ISIN>` so
  paper entry/exit marks use **that** symbol’s LTP / live candle close — never a
  shared synthetic base or another name’s quote.
- **Refresh:** `python scripts/refresh_nse_eq_instrument_keys.py`  
  (`--from-file /path/to/NSE.csv.gz` when offline)
- **Aliases:** `TATAMOTORS` → `TMPV` ISIN (legacy symbol in tests/docs)
