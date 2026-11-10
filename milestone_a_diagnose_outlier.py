#!/usr/bin/env python3
"""
MSFT's new-engine expectancy jumped to +522.6% / PF 129 in the Milestone A
comparison -- that is not a real result, it's almost certainly one broken
trade with a corrupted entry or exit price (likely a near-zero entry_px
from a bad band value somewhere in the new daily-sourced monthly series,
possibly from a split/adjustment mismatch between fetch() [weekly] and
fetch_daily() [daily]). This dumps every individual trade for the given
ticker(s) under both pipelines side by side so the outlier can be found
and the cause diagnosed BEFORE trusting any of the comparison numbers.

Usage:
    python3 milestone_a_diagnose_outlier.py --tickers MSFT
"""
import argparse
import pandas as pd
import backtest_33fvb as bt
from milestone_a_engine_fix import prep_v2

pd.set_option("display.width", 160)
pd.set_option("display.max_rows", 500)


def dump(tk):
    weekly = bt.fetch(tk)
    daily = bt.fetch_daily(tk)
    old = bt.prep(weekly)
    new, monthly_new = prep_v2(weekly, daily)

    trades_old = bt.run(old, "High", "NBarHigh")
    trades_new = bt.run(new, "High", "NBarHigh")

    print(f"\n{'='*100}\n{tk}: OLD trades ({len(trades_old)})\n{'='*100}")
    df_old = pd.DataFrame(trades_old)
    print(df_old.to_string(index=False))

    print(f"\n{'='*100}\n{tk}: NEW trades ({len(trades_new)})\n{'='*100}")
    df_new = pd.DataFrame(trades_new)
    print(df_new.to_string(index=False))

    if len(df_new):
        worst = df_new.loc[df_new["ret"].idxmax()]
        print(f"\n--- Biggest NEW-pipeline return: {worst['ret']*100:.1f}% "
              f"({worst['entry_date'].date()} -> {worst['exit_date'].date()}, {worst['reason']}) ---")
        ed, xd = worst["entry_date"], worst["exit_date"]
        # entry/exit week raw price + the band/basis values actually used
        cols = ["Open", "High", "Low", "Close", "basis", "upperR", "lowerR", "green"]
        print("\nNEW pipeline's weekly rows around entry:")
        print(new.loc[(new.index >= ed - pd.Timedelta(weeks=2)) & (new.index <= ed + pd.Timedelta(weeks=1)), cols].to_string())
        print("\nNEW pipeline's weekly rows around exit:")
        print(new.loc[(new.index >= xd - pd.Timedelta(weeks=1)) & (new.index <= xd + pd.Timedelta(weeks=1)), cols].to_string())
        print("\nOLD pipeline's weekly rows around the SAME entry date (for comparison):")
        print(old.loc[(old.index >= ed - pd.Timedelta(weeks=2)) & (old.index <= ed + pd.Timedelta(weeks=1)), cols].to_string())

        # sanity-check the monthly series itself around that date for
        # anything that looks broken (near-zero or absurdly large basis/band)
        m_window = monthly_new.loc[(monthly_new.index >= ed - pd.Timedelta(days=200))
                                    & (monthly_new.index <= ed + pd.Timedelta(days=60))]
        print("\nNEW pipeline's raw MONTHLY series around entry (basis/upperR/lowerR):")
        print(m_window.to_string())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", type=str, default="MSFT")
    args = ap.parse_args()
    for tk in args.tickers.split(","):
        dump(tk)


if __name__ == "__main__":
    main()
