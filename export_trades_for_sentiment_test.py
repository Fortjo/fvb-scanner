#!/usr/bin/env python3
"""
One-off export: full historical FVB trade list (High band + NBarHigh exit —
the same combo the live scanner uses) for the main UNIVERSE plus a bias-free
17-ETF basket, so Claude can test whether StockTwits sentiment/message-volume
at the entry month says anything about forward outcome.

This has to run on YOUR machine — this sandbox's yfinance access is blocked.
Just run it as-is:

    python3 export_trades_for_sentiment_test.py

It writes trades_for_sentiment_test.csv (ticker, universe, entry_date,
exit_date, ret, hold, reason) — then send me that file.
"""
import pandas as pd
import backtest_33fvb as bt

ETF_BASKET = ["SPY", "QQQ", "IWM", "XLB", "XLE", "XLF", "XLI", "XLK", "XLP",
              "XLU", "XLV", "XLY", "EFA", "EEM", "TLT", "IEF", "GLD"]

rows = []
all_tickers = [(tk, "stock") for tk in bt.UNIVERSE] + [(tk, "etf") for tk in ETF_BASKET]

for tk, uni in all_tickers:
    try:
        raw = bt.fetch(tk)
    except Exception as e:
        print(f"{tk}: fetch error {e}")
        continue
    if raw is None:
        print(f"{tk}: insufficient data")
        continue
    df = bt.prep(raw)
    if len(df) < bt.NBAR_LEN + 20:
        print(f"{tk}: too short after warmup")
        continue
    trades = bt.run(df, "High", "NBarHigh")
    for t in trades:
        rows.append({
            "ticker": tk, "universe": uni,
            "entry_date": t["entry_date"].strftime("%Y-%m-%d"),
            "exit_date": t["exit_date"].strftime("%Y-%m-%d"),
            "ret": t["ret"], "hold": t["hold"], "reason": t["reason"],
        })
    print(f"{tk}: {len(trades)} trades")

out = pd.DataFrame(rows)
out.to_csv("trades_for_sentiment_test.csv", index=False)
print(f"\nSaved {len(out)} trades to trades_for_sentiment_test.csv")
