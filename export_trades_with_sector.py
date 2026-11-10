#!/usr/bin/env python3
"""
Third candidate edge test: sector-relative strength. Instead of "is the
whole S&P healthy" (the already-validated market-timing filter), check
"is THIS STOCK'S OWN SECTOR healthy" at entry — same 10-month-SMA regime
test as market_regime.py, just run per sector instead of per SPY.

Theory: a stock flipping into an FVB buy zone while its own sector is in
a confirmed uptrend should do better than one flipping while its sector
is below its own 10-month average, even if the broad market (SPY) looks
fine — sector rotation can mask a lagging group inside a healthy index.

Method:
  1. Map each ticker in bt.UNIVERSE to a GICS sector via yfinance's own
     .info (no hand-maintained mapping — the ticker's actual classification).
  2. Map GICS sector name -> the matching Select Sector SPDR ETF
     (XLK, XLF, XLV, XLY, XLP, XLE, XLI, XLB, XLU, XLC, XLRE).
  3. For each of those 11 sector ETFs, compute the SAME monthly 10-month-
     SMA regime series used for SPY in market_regime.py / test_fvb_market_
     filter.py (last COMPLETED month, shifted — no lookahead).
  4. For every historical FVB trade (band="High", exit_mode="NBarHigh",
     same combo as the live scanner), look up whether that trade's own
     sector was ON or OFF at its entry date.
  5. Also export the 17-ETF bias-free basket's trades as a control group
     (no sector concept for the ETFs themselves — sector_regime_on is
     left blank for those rows).

Must run on YOUR machine — this sandbox's yfinance access is blocked.

    python3 export_trades_with_sector.py

Writes trades_with_sector.csv (ticker, universe, sector, entry_date,
exit_date, ret, hold, reason, sector_regime_on) — send me that file.
"""
import sys
import time
import pandas as pd
import backtest_33fvb as bt

ETF_BASKET = ["SPY", "QQQ", "IWM", "XLB", "XLE", "XLF", "XLI", "XLK", "XLP",
              "XLU", "XLV", "XLY", "EFA", "EEM", "TLT", "IEF", "GLD"]

SMA_MONTHS = 10

SECTOR_TO_ETF = {
    "Technology": "XLK",
    "Information Technology": "XLK",
    "Financial Services": "XLF",
    "Financials": "XLF",
    "Healthcare": "XLV",
    "Health Care": "XLV",
    "Consumer Cyclical": "XLY",
    "Consumer Discretionary": "XLY",
    "Consumer Defensive": "XLP",
    "Consumer Staples": "XLP",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Basic Materials": "XLB",
    "Materials": "XLB",
    "Utilities": "XLU",
    "Communication Services": "XLC",
    "Real Estate": "XLRE",
}


def get_sector_etf(ticker: str):
    import yfinance as yf
    try:
        info = yf.Ticker(ticker).info
        sector = info.get("sector")
    except Exception as e:
        print(f"{ticker}: sector lookup failed ({e})", file=sys.stderr)
        return None
    return SECTOR_TO_ETF.get(sector), sector


def monthly_regime(ticker: str):
    """Same construction as market_regime.py / spy_monthly_regime: monthly
    close vs its own 10-month SMA, using only the prior COMPLETED month,
    as a series you can .asof() against any entry date (weekly or daily)."""
    import yfinance as yf
    d = yf.download(ticker, start=bt.START, interval="1mo", auto_adjust=True, progress=False)
    if d is None or d.empty:
        return None
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    c = d["Close"].dropna()
    ok = (c > c.rolling(SMA_MONTHS).mean()).shift(1)
    return ok


rows = []

# --- Build sector ETF regime series once, reused across every ticker in
# that sector (avoids re-downloading the same ETF 10-20 times).
print("Fetching sector ETF regime series...", file=sys.stderr)
sector_regimes = {}
for etf in set(SECTOR_TO_ETF.values()):
    sector_regimes[etf] = monthly_regime(etf)
    print(f"  {etf}: {'ok' if sector_regimes[etf] is not None else 'FAILED'}", file=sys.stderr)
    time.sleep(0.3)

# --- Stock universe: look up each ticker's sector, then its trades.
for tk in bt.UNIVERSE:
    etf, sector_name = get_sector_etf(tk)
    if etf is None:
        print(f"{tk}: no sector mapping (sector={sector_name}), skipping sector tag", file=sys.stderr)
    regime = sector_regimes.get(etf) if etf else None

    try:
        df = bt.fetch(tk)
    except Exception as e:
        print(f"{tk}: fetch error {e}", file=sys.stderr)
        continue
    if df is None or len(df) < bt.FVB_LEN * 5:
        continue
    try:
        p = bt.prep(df)
        trades = bt.run(p, band="High", exit_mode="NBarHigh")
    except Exception as e:
        print(f"{tk}: run error {e}", file=sys.stderr)
        continue

    for t in trades:
        sector_on = None
        if regime is not None:
            try:
                val = regime.asof(t["entry_date"])
                sector_on = bool(val) if pd.notna(val) else None
            except Exception:
                sector_on = None
        rows.append({
            "ticker": tk, "universe": "stock", "sector": sector_name or "",
            "entry_date": t["entry_date"].strftime("%Y-%m-%d"),
            "exit_date": t["exit_date"].strftime("%Y-%m-%d"),
            "ret": t["ret"], "hold": t["hold"], "reason": t["reason"],
            "sector_regime_on": sector_on,
        })
    print(f"{tk}: {len(trades)} trades, sector={sector_name}", file=sys.stderr)

# --- ETF control basket: no sector concept, sector_regime_on left blank.
for tk in ETF_BASKET:
    try:
        df = bt.fetch(tk)
    except Exception as e:
        print(f"{tk}: fetch error {e}", file=sys.stderr)
        continue
    if df is None or len(df) < bt.FVB_LEN * 5:
        continue
    try:
        p = bt.prep(df)
        trades = bt.run(p, band="High", exit_mode="NBarHigh")
    except Exception as e:
        print(f"{tk}: run error {e}", file=sys.stderr)
        continue
    for t in trades:
        rows.append({
            "ticker": tk, "universe": "etf", "sector": "",
            "entry_date": t["entry_date"].strftime("%Y-%m-%d"),
            "exit_date": t["exit_date"].strftime("%Y-%m-%d"),
            "ret": t["ret"], "hold": t["hold"], "reason": t["reason"],
            "sector_regime_on": None,
        })
    print(f"{tk}: {len(trades)} trades (ETF control)", file=sys.stderr)

out = pd.DataFrame(rows)
out.to_csv("trades_with_sector.csv", index=False)
print(f"\nSaved {len(out)} trades to trades_with_sector.csv")
