#!/usr/bin/env python3
"""
Robustness check for the close_position finding from test_entry_confirmation.py
(close_position = where the entry bar closed within its own weekly range;
top quartile averaged +12.5%/trade vs +6.4% for the bottom quartile, a
clean monotonic trend across all 4 quartiles -- much larger and cleaner
than anything StochRSI showed).

Reuses the already-computed _with_confirmation.csv (no re-fetch, no
network needed) and checks:

  1. OUT-OF-SAMPLE: same --split convention as backtest_33fvb.py. Splits
     trades into train (before split date) / test (on or after). Picks
     the median close_position from TRAIN ONLY as the cutoff, applies
     that same fixed cutoff to TEST, and reports both segments' above-
     vs-below stats. A real effect should hold on test using a cutoff
     frozen before test data was ever looked at.
  2. YEARLY CONCENTRATION: per entry year, splits by that same train
     cutoff and reports expectancy for above/below, so a result driven
     by one or two outlier years is visible instead of hidden in one
     aggregate number.
  3. CORRELATION with lower_wick_ratio and vol_ratio, to check whether
     idea 1b was really an independent signal or just echoing idea 1a.

Usage:
    python3 test_close_position_robustness.py milestone_c_sp500_baseline_trades_with_confirmation.csv
"""
import sys
import numpy as np
import pandas as pd
from test_stochrsi_filter import bucket_stats


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 test_close_position_robustness.py <with_confirmation_csv>", file=sys.stderr)
        sys.exit(1)
    path = sys.argv[1]
    split_date = pd.Timestamp(sys.argv[2]) if len(sys.argv) > 2 else pd.Timestamp("2023-01-01")

    trades = pd.read_csv(path, parse_dates=["entry_date", "exit_date"])
    ret_col = "net_ret_pct" if "net_ret_pct" in trades.columns else "ret_pct"
    trades = trades[trades["close_position"].notna()].copy()

    print(f"=== CORRELATION (idea 1a vs idea 1b vs idea 2) ===")
    corr = trades[["close_position", "lower_wick_ratio", "vol_ratio"]].corr()
    print(corr.to_string())

    train = trades[trades["entry_date"] < split_date]
    test = trades[trades["entry_date"] >= split_date]
    cutoff = train["close_position"].median()
    print(f"\nSplit date: {split_date.date()}  |  train: {len(train)} trades, test: {len(test)} trades")
    print(f"Cutoff (train median close_position): {cutoff:.3f}")

    print("\n=== OUT-OF-SAMPLE: above vs below train-median cutoff ===")
    for label, seg in [("TRAIN", train), ("TEST (out-of-sample)", test)]:
        below = seg[seg["close_position"] < cutoff]
        above = seg[seg["close_position"] >= cutoff]
        sb, sa = bucket_stats(below[ret_col].values), bucket_stats(above[ret_col].values)
        print(f"\n{label}:")
        print(f"  BELOW cutoff: {sb['trades']:4d} trades, win {sb['win_rate']:.1f}%, "
              f"PF {sb['pf']:.2f}, exp {sb['expectancy']:+.2f}%")
        print(f"  ABOVE cutoff: {sa['trades']:4d} trades, win {sa['win_rate']:.1f}%, "
              f"PF {sa['pf']:.2f}, exp {sa['expectancy']:+.2f}%")
        if sb["trades"] and sa["trades"]:
            holds = sa["expectancy"] > sb["expectancy"]
            print(f"  -> {'HOLDS' if holds else 'DOES NOT HOLD'} on this segment")

    print("\n=== YEARLY CONCENTRATION (same train-median cutoff, all years) ===")
    yearly = trades.copy()
    yearly["entry_year"] = yearly["entry_date"].dt.year
    yearly["above_cutoff"] = yearly["close_position"] >= cutoff
    rows = []
    for yr, grp in yearly.groupby("entry_year"):
        below = grp[~grp["above_cutoff"]]
        above = grp[grp["above_cutoff"]]
        sb, sa = bucket_stats(below[ret_col].values), bucket_stats(above[ret_col].values)
        rows.append({"year": yr, "total": len(grp),
                     "below_n": sb["trades"], "below_exp_pct": round(sb["expectancy"], 2) if sb["trades"] else np.nan,
                     "above_n": sa["trades"], "above_exp_pct": round(sa["expectancy"], 2) if sa["trades"] else np.nan})
    yr_table = pd.DataFrame(rows).sort_values("year").reset_index(drop=True)
    print(yr_table.to_string(index=False))

    better_years = (yr_table["above_exp_pct"] > yr_table["below_exp_pct"]).sum()
    total_years = yr_table[["above_exp_pct", "below_exp_pct"]].dropna().shape[0]
    print(f"\n'above cutoff' beat 'below cutoff' in {better_years}/{total_years} years with both buckets populated.")


if __name__ == "__main__":
    main()
