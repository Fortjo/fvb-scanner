#!/usr/bin/env python3
"""
Does the "oversold, turning up" StochRSI readout predict a BIGGER bounce,
even if it doesn't predict a higher win rate?

test_stochrsi_filter.py already showed the readout does NOT improve win
rate or average expectancy (87 trades w/ readout: 66.7% win, +4.83% exp;
952 trades w/o: 66.3% win, +9.37% exp -- readout trades did WORSE on
average, not better). That's a comparison of MEANS. It leaves open a
different, narrower claim: maybe among trades that already won, the ones
with the readout won by more (a bigger bounce), or exited faster, even if
the readout doesn't change whether a trade wins at all.

This checks that narrower claim directly:
  1. Win rate (repeated from test_stochrsi_filter.py, for context).
  2. Average and median size of WINS ONLY, with vs without the readout.
  3. Average and median size of LOSSES ONLY, with vs without the readout.
  4. Median holding period (exit_date - entry_date), with vs without.
  5. Same four comparisons using the raw continuous %K value split into
     quintiles (finer-grained than one yes/no readout), so a real "more
     oversold -> bigger bounce" relationship would show as a trend across
     quintiles even if the binary cross/turn-up flag is too strict to
     catch it.

Needs network (yfinance) -- run on a machine with real network access.

Usage:
    python3 test_stochrsi_magnitude.py milestone_c_sp500_baseline_trades.csv
"""
import sys
import numpy as np
import pandas as pd
import backtest_33fvb as bt
from test_stochrsi_filter import compute_stoch_rsi, oversold_turning_up
from test_stochrsi_variants import entry_week_index


def fetch_flags_and_k(trades: pd.DataFrame) -> pd.DataFrame:
    trades = trades.copy()
    tickers = sorted(trades["ticker"].unique())
    print(f"{len(trades)} trades across {len(tickers)} tickers. Fetching weekly price history...",
          file=sys.stderr)

    flag_by_idx, k_by_idx = {}, {}
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
                flag_by_idx[idx] = None
                k_by_idx[idx] = None
            continue
        k, d = compute_stoch_rsi(weekly["Close"])
        for idx, row in sub.iterrows():
            entry_week_i, i = entry_week_index(weekly.index, row["entry_date"])
            flag_by_idx[idx] = oversold_turning_up(k, d, i) if i >= 0 else False
            k_by_idx[idx] = float(k.iloc[i]) if 0 <= i < len(k) and not pd.isna(k.iloc[i]) else None
        print(f"[{n}/{len(tickers)}] {tk}: done", file=sys.stderr)

    trades["oversold_turning_up"] = trades.index.map(flag_by_idx.get)
    trades["raw_k"] = trades.index.map(k_by_idx.get)
    return trades


def describe_bucket(label: str, sub: pd.DataFrame, ret_col: str):
    if len(sub) == 0:
        print(f"  {label}: 0 trades")
        return
    r = sub[ret_col]
    wins, losses = r[r > 0], r[r <= 0]
    hold_days = (pd.to_datetime(sub["exit_date"]) - pd.to_datetime(sub["entry_date"])).dt.days
    print(f"  {label}: {len(sub)} trades, win rate {100*len(wins)/len(sub):.1f}%")
    if len(wins):
        print(f"    wins   ({len(wins):4d}): avg +{wins.mean():.2f}%, median +{wins.median():.2f}%, "
              f"max +{wins.max():.2f}%")
    if len(losses):
        print(f"    losses ({len(losses):4d}): avg {losses.mean():.2f}%, median {losses.median():.2f}%, "
              f"worst {losses.min():.2f}%")
    print(f"    median holding period: {hold_days.median():.0f} days")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 test_stochrsi_magnitude.py <trades_csv>", file=sys.stderr)
        sys.exit(1)
    trades_path = sys.argv[1]
    trades = pd.read_csv(trades_path, parse_dates=["entry_date", "exit_date"])
    ret_col = "net_ret_pct" if "net_ret_pct" in trades.columns else "ret_pct"
    if ret_col not in trades.columns:
        print(f"Couldn't find a return column in {trades_path}. Columns: {list(trades.columns)}",
              file=sys.stderr)
        sys.exit(1)

    trades = fetch_flags_and_k(trades)
    matched = trades[trades["oversold_turning_up"].notna()]

    print("\n=== BOUNCE SIZE: oversold-turning-up readout vs not ===")
    describe_bucket("WITH oversold-turning-up readout", matched[matched["oversold_turning_up"] == True], ret_col)
    describe_bucket("WITHOUT (everything else)", matched[matched["oversold_turning_up"] == False], ret_col)

    print("\n=== BOUNCE SIZE BY RAW %K QUINTILE (lower %K = more oversold) ===")
    valid_k = trades[trades["raw_k"].notna()].copy()
    if len(valid_k) >= 5:
        valid_k["k_quintile"] = pd.qcut(valid_k["raw_k"], 5, duplicates="drop")
        for q, grp in valid_k.groupby("k_quintile", observed=True):
            describe_bucket(f"%K in {q}", grp, ret_col)
    else:
        print("Not enough valid %K values for a quintile breakdown.")

    out_path = trades_path.rsplit(".", 1)[0] + "_with_magnitude.csv"
    trades.to_csv(out_path, index=False)
    print(f"\nSaved: {out_path}")
    print("\nRead this as: if the readout (or lower %K) marks a genuinely 'riper' entry, wins should be "
          "BIGGER (not just as frequent) and/or losses should be SMALLER/faster to cut, with the readout "
          "on. If the win/loss sizes and holding periods look similar across buckets, the readout isn't "
          "adding timing information -- it's just splitting the same population randomly.")


if __name__ == "__main__":
    main()
