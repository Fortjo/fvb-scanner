#!/usr/bin/env python3
"""
Export the full trade history with a per-trade SPY-regime flag attached,
so the repaired portfolio-simulation engine (fixed per an independent
quant audit: real elapsed-time compounding, non-stale equity sizing,
proper weekly Sharpe) can be rerun on the market-timing filter WITHOUT
needing live yfinance access each time.

Same universe/trade combo as every other export in this project: bt.UNIVERSE
(170 stocks) + the 17-ETF bias-free control basket, band="High", exit_mode=
"NBarHigh". spy_regime_on is SPY's close vs its own 10-month SMA, using
only the last COMPLETED month at the time of entry (no lookahead).

Must run on YOUR machine — this sandbox's yfinance access is blocked.

    python3 export_trades_with_spy_regime.py

Writes trades_with_spy_regime.csv (ticker, universe, entry_date, exit_date,
ret, hold, reason, spy_regime_on) — send me that file.
"""
import sys
import pandas as pd
import backtest_33fvb as bt

ETF_BASKET = ["SPY", "QQQ", "IWM", "XLB", "XLE", "XLF", "XLI", "XLK", "XLP",
              "XLU", "XLV", "XLY", "EFA", "EEM", "TLT", "IEF", "GLD"]

SMA_MONTHS = 10


def spy_monthly_regime():
    import yfinance as yf
    d = yf.download("SPY", start=bt.START, interval="1mo", auto_adjust=True, progress=False)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    c = d["Close"].dropna()
    ok = (c > c.rolling(SMA_MONTHS).mean()).shift(1)
    return ok


print("Fetching SPY regime series...", file=sys.stderr)
regime = spy_monthly_regime()

rows = []
all_tickers = [(tk, "stock") for tk in bt.UNIVERSE] + [(tk, "etf") for tk in ETF_BASKET]

for tk, uni in all_tickers:
    try:
        raw = bt.fetch(tk)
    except Exception as e:
        print(f"{tk}: fetch error {e}", file=sys.stderr)
        continue
    if raw is None or len(raw) < bt.FVB_LEN * 5:
        continue
    try:
        df = bt.prep(raw)
        trades = bt.run(df, "High", "NBarHigh")
    except Exception as e:
        print(f"{tk}: run error {e}", file=sys.stderr)
        continue
    for t in trades:
        try:
            val = regime.asof(t["entry_date"])
            spy_on = bool(val) if pd.notna(val) else None
        except Exception:
            spy_on = None
        rows.append({
            "ticker": tk, "universe": uni,
            "entry_date": t["entry_date"].strftime("%Y-%m-%d"),
            "exit_date": t["exit_date"].strftime("%Y-%m-%d"),
            "ret": t["ret"], "hold": t["hold"], "reason": t["reason"],
            "spy_regime_on": spy_on,
        })
    print(f"{tk}: {len(trades)} trades", file=sys.stderr)

out = pd.DataFrame(rows)
out.to_csv("trades_with_spy_regime.csv", index=False)
print(f"\nSaved {len(out)} trades to trades_with_spy_regime.csv")
