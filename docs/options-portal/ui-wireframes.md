# UI wireframes (markdown mockups)

Local web portal — desktop-first, usable on tablet. Currency in ₹. Times in IST. Mode badge always visible: `DRY-RUN` | `DEMO` | `LIVE`.

India examples throughout: **NIFTY**, **BANKNIFTY**, **RELIANCE**, **TCS**; signals on **15m** candles; index **weekly** vs stock **monthly** expiry callouts.

---

## 1. Dashboard

```text
┌──────────────────────────────────────────────────────────────────────────┐
│  Options Portal          IST 15:29   [DRY-RUN]   Health ● OK   user▾    │
├──────────────────────────────────────────────────────────────────────────┤
│  Watchlist signals · 15m · full F&O · actionable first                   │
│                                                                          │
│  Symbol      Action        Reason        Hybrid  Tech  News  Expiry     │
│  NIFTY       BUY_CE_BIAS   EMA+RSI+news   +0.62  +0.71 +0.38 weekly     │
│  RELIANCE    BUY_PE_BIAS   EMA+VWAP+news  -0.55  -0.48 -0.67 monthly    │
│  BANKNIFTY   HOLD          neutral        +0.12  +0.20 -0.05 weekly     │
│  TCS         HOLD          news veto      -0.08  +0.10 -0.35 monthly    │
│                                                                          │
│  Hover Reason -> popup with reason_detail (indicators + headlines)       │
│  [Filter symbol]  [Prev] 1/5 [Next]                                      │
└──────────────────────────────────────────────────────────────────────────┘
```

Notes: one dense table; actionable rows sort above HOLDs; Reason chip uses API `reason_summary` with hover/focus popup from `reason_detail`.

---

## 2. Signal drill-down (e.g. NIFTY)

```text
┌──────────────────────────────────────────────────────────────────────────┐
│  ← Dashboard   NIFTY · 15m · bar 15:15 IST              [DRY-RUN]       │
├──────────────────────────────────────────────────────────────────────────┤
│  Action: BUY_CE_BIAS     Hybrid +0.62 (conf 0.74)                        │
│  Tech +0.71 · News +0.38 · No veto                                       │
│                                                                          │
│  Reasons                                                                 │
│  • EMA9 > EMA21 on 15m                                                   │
│  • RSI(14) 58 — rising                                                   │
│  • Supertrend bullish                                                    │
│  • News: mild positive (Moneycontrol, ET) last 3h                        │
│                                                                          │
│  Recommendation                                                          │
│  BUY NIFTY 21-Nov 24500 CE × 1 lot (25)  limit ₹142.50                   │
│  Spot ref 24,486 · ATM±0 · OI OK · spread 0.8%                           │
│  [Simulate order]   (Live disabled for analyst)                          │
│                                                                          │
│  Attached news                                                           │
│  +0.4  ET        14:52  Foreign inflows lift benchmark…                  │
│  +0.2  MC        13:10  Nifty holds above VWAP into close…               │
│                                                                          │
│  Feature snapshot (JSON expandable): rsi=58.2 ema_fast=24490 …           │
└──────────────────────────────────────────────────────────────────────────┘
```

Optional later: sparkline of last 40×15m bars (not required for v1 wireframe).

---

## 3. News feed

```text
┌──────────────────────────────────────────────────────────────────────────┐
│  News · sentiment                                       [DRY-RUN]        │
├──────────────────────────────────────────────────────────────────────────┤
│  Sources: [x] Moneycontrol [x] ET [x] LiveMint [x] NSE [x] BSE [x] SEBI │
│  Symbols: [NIFTY] [BANKNIFTY] [RELIANCE] [TCS] [All]                     │
│                                                                          │
│  Sent   Syms              Title                        Source   Time IST │
│  −0.72  RELIANCE          Broker cuts target on…       LiveMint 15 Nov…  │
│  +0.41  NIFTY,BANKNIFTY   Foreign inflows lift…        ET       15 Nov…  │
│   0.05  TCS               Quiet session ahead of…      MC       15 Nov…  │
│  −0.30  —                 SEBI circular: margin…       SEBI     15 Nov…  │
│                                                                          │
│  ↕ vertical scroll for long live lists (today IST only; history off-UI)  │
│  Click row → article detail + linked signals                             │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Trades / P&L

```text
┌──────────────────────────────────────────────────────────────────────────┐
│  Trades & P&L · mode [DRY-RUN ▾]                                         │
├──────────────────────────────────────────────────────────────────────────┤
│  Open positions                                                          │
│  NIFTY 21-Nov 24500 CE   +25 qty   avg ₹138.00   uPnL ₹ +1,125          │
│  RELIANCE 28-Nov 2920 PE +250 qty  avg ₹64.50    uPnL ₹ −2,400          │
│                                                                          │
│  Closed trades (round-trip)                                              │
│  RELIANCE  LONG  11:02→15:16  2500.00→2512.50  +12.50  signal_exit      │
│  TCS       SHORT 10:05→14:40  3900.00→3888.00  +12.00  cover short      │
│                                                                          │
│  Day realized ₹ +3,100   Day unrealized ₹ −1,275   Fees est ₹ 180        │
│                                                                          │
│  [Export CSV]                                                            │
└──────────────────────────────────────────────────────────────────────────┘
```

Guest role: hide fee/P&L breakdown if config `ui.guest_hide_pnl: true`.

---

## 5. Admin config (admin only)

```text
┌──────────────────────────────────────────────────────────────────────────┐
│  Admin · configuration                                                   │
├──────────────────────────────────────────────────────────────────────────┤
│  Trading mode     ( ) dry_run  ( ) demo  (•) live   ⚠ requires confirm   │
│  Kill switch      [ HALT ORDERS ]                                        │
│                                                                          │
│  Symbols          NIFTY, BANKNIFTY, RELIANCE, TCS                        │
│  Timeframe        15m                                                    │
│                                                                          │
│  Hybrid weights   tech 0.65   news 0.35   veto |news|>0.7                │
│  Expiry           index: weekly   stocks: monthly   cut-off: 15:00 IST   │
│                                                                          │
│  Risk             max_lots/symbol 2   max_day_loss ₹25,000               │
│                   no new entries after 15:20 IST                         │
│                                                                          │
│  Sentiment        VADER on   FinBERT [enabled] endpoint http://finbert:8080 │
│                                                                          │
│  [Save version]  last change by admin @ 10:12 IST (v14)                  │
└──────────────────────────────────────────────────────────────────────────┘
```

Live mode toggle: modal “Type LIVE to confirm” + audit event.

---

## 6. Health

```text
┌──────────────────────────────────────────────────────────────────────────┐
│  System health                                            refreshed 5s   │
├──────────────────────────────────────────────────────────────────────────┤
│  Component          Status     Latency   Detail                          │
│  API                ● ok       12 ms     /health/ready                   │
│  Postgres           ● ok       3 ms                                      │
│  Redis              ● ok       1 ms                                      │
│  Worker heartbeat   ● ok       —         last tick 15:15:02 IST          │
│  Upstox auth        ● ok       80 ms     token exp 18:40 IST             │
│  Upstox candles     ● ok       —         NIFTY 15m age 14m               │
│  RSS Moneycontrol   ● ok       —         last item 15:01                 │
│  RSS ET             ● degraded —         2 parse errors / 1h             │
│  FinBERT sidecar    ● ok       45 ms                                     │
│                                                                          │
│  Recent errors (audit)                                                   │
│  14:44  upstox 429 backoff 4s                                            │
│  13:12  rss:et timeout — quarantined 10m                                 │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Navigation IA

```text
Dashboard | Signals | News | Trades | Health | Admin*
* Admin visible only to admin role
```

Mobile: collapse table to stacked symbol rows; mode + health remain in sticky header.
