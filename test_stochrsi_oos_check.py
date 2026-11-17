#!/usr/bin/env python3
"""
Out-of-sample check for the avoid-if-oversold StochRSI idea, using the
SAME train/test split convention backtest_33fvb.py already uses
(--split, default 2023-01-01: everything before is "train", everything
from that date on is "test" -- see backtest_33fvb.main()'s own docstring:
"if test numbers collapse relative to train, the strategy was overfit").

Why this matters here specifically: test_stochrsi_avoid_filter.py's result
(keep-beats-avoid across all 5 thresholds and both lag choices, but a
roughly 50/50 split of which YEARS it holds in) came from looking at the
same 1,039 trades from several angles in a row -- a real but modest
pattern found that way deserves one true held-out check before it's
trusted, not another in-sample cut. This is that check: does the pattern
still hold on entries the earlier exploration never looked at as its own
segment?

Reuses fetch_k_references() and threshold_lag_table() from
test_stochrsi_avoid_filter.py -- one fetch pass, then both segments are
just a date filter on the already-fetched data (no extra network calls).

Needs network -- run on a machine with it.

Usage:
    python3 test_stochrsi_oos_check.py milestone_c_sp500_baseline_trades.csv
    python3 test_stochrsi_oos_check.py milestone_c_sp500_baseline_trades.csv --split 2023-01-01
"""
import sys
import argparse
import pandas as pd
from test_stochrsi_avoid_filter import fetch_k_references, threshold_lag_table, bucket_stats


def split_report(trades: pd.DataFrame, ret_col: str, split_date: pd.Timestamp):
    """Prints the threshold x lag table separately for TRAIN (entry_date <
    split_date) and TEST (entry_date >= split_date), plus a one-line
    verdict at the conventional threshold=20 for both reference points."""
    train = trades[trades["entry_date"] < split_date]
    test = trades[trades["entry_date"] >= split_date]
    print(f"\nSplit date: {split_date.date()}  |  train: {len(train)} trades, "
          f"test (out-of-sample): {len(test)} trades")

    for label, seg in [("TRAIN", train), ("TEST (out-of-sample)", test)]:
        print(f"\n=== {label}: THRESHOLD x LAG ===")
        if len(seg) == 0:
            print("  (no trades in this segment)")
            continue
        t = threshold_lag_table(seg, ret_col)
        print(t.to_string(index=False))

    print("\n=== VERDICT at threshold=20 (the conventional cutoff) ===")
    any_test_rows = False
    for ref_label in ["1wk-lag", "entry-wk"]:
        t_train = threshold_lag_table(train, ret_col)
        t_test = threshold_lag_table(test, ret_col)
        row_train = t_train[(t_train["reference"] == ref_label) & (t_train["threshold"] == 20)]
        row_test = t_test[(t_test["reference"] == ref_label) & (t_test["threshold"] == 20)]
        if len(row_test) == 0 or row_test.iloc[0]["avoid_n"] == 0 or row_test.iloc[0]["keep_n"] == 0:
            print(f"[{ref_label}] TEST segment has too few trades in one bucket to judge.")
            continue
        any_test_rows = True
        rt, re = row_train.iloc[0], row_test.iloc[0]
        holds = re["keep_exp_pct"] > re["avoid_exp_pct"]
        print(f"[{ref_label}] TRAIN: avoid {rt['avoid_n']} trades exp {rt['avoid_exp_pct']:+.2f}% vs "
              f"keep {rt['keep_n']} trades exp {rt['keep_exp_pct']:+.2f}%")
        print(f"[{ref_label}] TEST:  avoid {re['avoid_n']} trades exp {re['avoid_exp_pct']:+.2f}% vs "
              f"keep {re['keep_n']} trades exp {re['keep_exp_pct']:+.2f}%  "
              f"-> {'HOLDS out-of-sample' if holds else 'DOES NOT HOLD out-of-sample'}")
    if not any_test_rows:
        print("Not enough out-of-sample trades in either bucket to draw a conclusion -- "
              "the test period may simply be too short/thin for this particular check.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("trades_csv")
    ap.add_argument("--split", type=str, default="2023-01-01",
                     help="Train/test split date, same convention as backtest_33fvb.py --split "
                          "(default 2023-01-01).")
    args = ap.parse_args()

    trades = pd.read_csv(args.trades_csv, parse_dates=["entry_date", "exit_date"])
    ret_col = "net_ret_pct" if "net_ret_pct" in trades.columns else "ret_pct"
    if ret_col not in trades.columns:
        print(f"Couldn't find a return column in {args.trades_csv}. Columns: {list(trades.columns)}",
              file=sys.stderr)
        sys.exit(1)

    trades = fetch_k_references(trades)
    split_report(trades, ret_col, pd.Timestamp(args.split))

    out_path = args.trades_csv.rsplit(".", 1)[0] + "_oos_check.csv"
    trades.to_csv(out_path, index=False)
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
