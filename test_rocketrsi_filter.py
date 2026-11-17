#!/usr/bin/env python3
"""
Does Ehlers' RocketRSI (rocket_rsi.py -- see its docstring for the
formula and why it's a faithful stand-in for the locked "RSI KILLER"
TradingView script) predict anything about Forty's real 33FVB trades?

RocketRSI is designed to spike NEGATIVE at cyclic bottoms and POSITIVE
at cyclic tops. On a weekly SMZ-touch entry (which is itself already a
"we're near a support zone" event), a deep negative spike would mean
"this isn't just near support, the cycle itself says we're at a
bottom right now" -- testing whether that's informative the same way
test_stochrsi_filter.py tested the "oversold, turning up" idea.

Same no-lookahead convention as every other indicator test in this
project: the value used is from the LAST FULLY COMPLETED weekly bar
strictly before the entry week (reuses entry_week_index from
test_stochrsi_variants.py).

Reports both:
  1. Quintile breakdown (continuous) -- does win rate/expectancy trend
     smoothly as RocketRSI goes from very negative (bottom signal) to
     very positive (top signal)?
  2. A simple "deep negative spike" binary split (RocketRSI below a
     threshold vs not) -- the literal "is this a bottom" reading.

Needs network (yfinance) -- run on a machine with real network access.

Usage:
    python3 test_rocketrsi_filter.py milestone_c_sp500_baseline_trades.csv
"""
from __future__ import annotations
import sys
import numpy as np
import pandas as pd
import backtest_33fvb as bt
from rocket_rsi import compute_rocket_rsi
from test_stochrsi_filter import bucket_stats
from test_stochrsi_variants import entry_week_index

SPIKE_THRESHOLD = -1.5   # a deep negative RocketRSI reading = "cyclic bottom" per Ehlers


def fetch_rocket_rsi(trades: pd.DataFrame) -> pd.DataFrame:
    trades = trades.copy()
    tickers = sorted(trades["ticker"].unique())
    print(f"{len(trades)} trades across {len(tickers)} tickers. Fetching weekly price history...",
          file=sys.stderr)

    rr_by_idx = {}
    for n, tk in enumerate(tickers, 1):
        sub = trades[trades["ticker"] == tk]
        try:
            weekly = bt.fetch(tk)
        except Exception as e:
            print(f"{tk}: fetch error {e}", file=sys.stderr)
            weekly = None
        if weekly is None or len(weekly) < 50:
            print(f"{tk}: insufficient data, leaving {len(sub)} trade(s) unflagged", file=sys.stderr)
            for idx in sub.index:
                rr_by_idx[idx] = None
            continue
        rr = compute_rocket_rsi(weekly["Close"])
        for idx, row in sub.iterrows():
            _, i = entry_week_index(weekly.index, row["entry_date"])
            rr_by_idx[idx] = float(rr.iloc[i]) if 0 <= i < len(rr) and not pd.isna(rr.iloc[i]) else None
        print(f"[{n}/{len(tickers)}] {tk}: done", file=sys.stderr)

    trades["rocket_rsi"] = trades.index.map(rr_by_idx.get)
    return trades


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 test_rocketrsi_filter.py <trades_csv>", file=sys.stderr)
        sys.exit(1)
    trades_path = sys.argv[1]
    trades = pd.read_csv(trades_path, parse_dates=["entry_date", "exit_date"])
    ret_col = "net_ret_pct" if "net_ret_pct" in trades.columns else "ret_pct"
    if ret_col not in trades.columns:
        print(f"Couldn't find a return column in {trades_path}. Columns: {list(trades.columns)}",
              file=sys.stderr)
        sys.exit(1)

    trades = fetch_rocket_rsi(trades)
    valid = trades[trades["rocket_rsi"].notna()].copy()
    print(f"\nMatched {len(valid)}/{len(trades)} trades with usable price history.")

    print("\n=== QUINTILE BREAKDOWN (very negative = 'bottom' per Ehlers, very positive = 'top') ===")
    valid["q"] = pd.qcut(valid["rocket_rsi"], 5, duplicates="drop")
    for q, grp in valid.groupby("q", observed=True):
        s = bucket_stats(grp[ret_col].values)
        print(f"  rocket_rsi in {q}: {s['trades']:4d} trades, win {s['win_rate']:.1f}%, "
              f"PF {s['pf']:.2f}, exp {s['expectancy']:+.2f}%")

    print(f"\n=== DEEP NEGATIVE SPIKE (<{SPIKE_THRESHOLD}, Ehlers' literal 'cyclic bottom' reading) vs not ===")
    spike = valid[valid["rocket_rsi"] < SPIKE_THRESHOLD]
    no_spike = valid[valid["rocket_rsi"] >= SPIKE_THRESHOLD]
    for label, sub in [("SPIKE (bottom signal)", spike), ("NO SPIKE (everything else)", no_spike)]:
        s = bucket_stats(sub[ret_col].values)
        if s["trades"] == 0:
            print(f"  {label}: 0 trades")
            continue
        print(f"  {label}: {s['trades']} trades, win rate {s['win_rate']:.1f}%, "
              f"PF {s['pf']:.2f}, expectancy {s['expectancy']:+.2f}%/trade")

    out_path = trades_path.rsplit(".", 1)[0] + "_with_rocketrsi.csv"
    trades.to_csv(out_path, index=False)
    print(f"\nSaved: {out_path}")
    print("\nRead the quintile table for a trend (does expectancy climb or fall smoothly from 'top' to "
          "'bottom' readings, not just jump around), and the spike split for Ehlers' own literal claim "
          "(a deep negative spike = this IS a cyclic bottom right now). Same bar every other indicator "
          "in this project was held to: a real effect should be a trend, not a fluke in one slice.")


if __name__ == "__main__":
    main()
