#!/usr/bin/env python3
"""
Second candidate edge test: does the WEEKLY VOLUME on the entry bar — the
week price actually crosses into the FVB band and flips green — predict
anything about the trade's outcome? Classic breakout theory: a flip on
above-average volume should follow through more often than a flip on thin
volume.

Exports every historical FVB trade (High band + NBarHigh exit, same combo
the live scanner uses), same universe as the earlier exports (main UNIVERSE
+ 17-ETF bias-free basket), plus for each trade: the entry bar's own weekly
volume, and the average weekly volume over the preceding 10 weeks (NOT
including the entry bar itself, so this is a point-in-time number with no
lookahead — exactly what you could have known the week you entered).

Must run on YOUR machine — this sandbox's yfinance access is blocked.

    python3 export_trades_with_volume.py

Writes trades_with_volume.csv (ticker, universe, entry_date, exit_date, ret,
hold, reason, entry_volume, avg_volume_10wk_prior, vol_ratio) — send me that
file.
"""
import pandas as pd
import backtest_33fvb as bt

ETF_BASKET = ["SPY", "QQQ", "IWM", "XLB", "XLE", "XLF", "XLI", "XLK", "XLP",
              "XLU", "XLV", "XLY", "EFA", "EEM", "TLT", "IEF", "GLD"]

VOL_LOOKBACK = 10  # weeks of trailing volume average, excluding entry bar


def fetch_with_volume(ticker: str):
    import yfinance as yf
    df = yf.download(ticker, start=bt.START, interval="1wk",
                      auto_adjust=True, progress=False)
    if df is None or len(df) < bt.FVB_LEN * 5:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df[["Open", "High", "Low", "Close", "Volume"]].dropna()


rows = []
all_tickers = [(tk, "stock") for tk in bt.UNIVERSE] + [(tk, "etf") for tk in ETF_BASKET]

for tk, uni in all_tickers:
    try:
        raw = fetch_with_volume(tk)
    except Exception as e:
        print(f"{tk}: fetch error {e}")
        continue
    if raw is None:
        print(f"{tk}: insufficient data")
        continue
    df = bt.prep(raw)   # prep() copies every input column through untouched, Volume included
    if len(df) < bt.NBAR_LEN + 20:
        print(f"{tk}: too short after warmup")
        continue
    trades = bt.run(df, "High", "NBarHigh")
    vol = df["Volume"]
    for t in trades:
        ed = t["entry_date"]
        try:
            loc = df.index.get_loc(ed)
        except KeyError:
            continue
        if loc < VOL_LOOKBACK:
            continue  # not enough prior history for a trailing average
        entry_vol = float(vol.iloc[loc])
        avg_vol = float(vol.iloc[loc - VOL_LOOKBACK:loc].mean())
        rows.append({
            "ticker": tk, "universe": uni,
            "entry_date": ed.strftime("%Y-%m-%d"),
            "exit_date": t["exit_date"].strftime("%Y-%m-%d"),
            "ret": t["ret"], "hold": t["hold"], "reason": t["reason"],
            "entry_volume": entry_vol,
            "avg_volume_10wk_prior": avg_vol,
            "vol_ratio": entry_vol / avg_vol if avg_vol > 0 else None,
        })
    print(f"{tk}: {len(trades)} trades")

out = pd.DataFrame(rows)
out.to_csv("trades_with_volume.csv", index=False)
print(f"\nSaved {len(out)} trades to trades_with_volume.csv")
