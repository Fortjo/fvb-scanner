#!/usr/bin/env python3
"""
Milestone A, additional check requested by the auditor after round 2:

  "Verify that the monthly FVB state used for a weekly trading decision
   was available at the beginning of the relevant execution period, not
   merely by the weekly candle's timestamp. For each weekly candle,
   establish its actual first and last trading dates. Explicitly test
   the first trading week following month-end, including weeks whose
   Yahoo Finance label falls in the previous month. Do not assume that a
   Sunday or Monday weekly label represents the time when the weekly
   OHLC became available. Report any resulting signal-timing
   discrepancies separately from the monthly OHLC construction
   differences."

This is a NARROWER and more direct question than Fix #5 (which only fixed
how the monthly OHLC itself is built, from daily bars instead of weekly).
This script isolates a different question: milestone_a_engine_fix.py's
prep_v2() attaches the monthly indicator to each weekly bar by reindexing
on the weekly bar's own LABEL ("mm = monthly.reindex(df_weekly.index,
method='ffill')"). That is only correct if the label date is always >=
the bar's actual first trading date -- which the timestamp-convention
script already showed is NOT always true (Sunday-anchored eras label a
bar BEFORE its real trading days even begin). This script checks, bar by
bar, whether that label-based cutoff ever disagrees with the cutoff you'd
get from the bar's ACTUAL first trading date -- and reports that as a
distinct "signal-timing" discrepancy, separate from the OHLC-construction
comparison (green-state-differs-on-X%-of-weeks) already reported.

Method, per bar:
  1. Determine the bar's real trading days from the raw daily series --
     NOT assumed from the label's weekday -- using the window between
     this bar's label and the NEXT bar's label ([label_i, label_{i+1})).
     This self-corrects for the Sunday vs Monday anchor shift found
     empirically: whichever convention is in effect, the true trading
     days for bar i are whatever actually falls between consecutive
     labels, regardless of which weekday those labels are.
  2. exec_date = the EARLIEST real trading day in that window -- i.e. the
     actual first moment execution of that week's decision could happen.
  3. label_month = the latest completed month whose month-end date is
     <= the bar's LABEL (what prep_v2 currently uses).
  4. exec_month = the latest completed month whose month-end date is
     < exec_date (what should be used if decisions were made "at the
     beginning of the execution period" using only data truly available
     by then).
  5. Flag every bar where label_month != exec_month. Direction matters:
       - label_month is a LATER month than exec_month: label-based
         logic would use a month's data before that month's completion
         was actually knowable as of the real start of trading -- a
         genuine lookahead risk, however small.
       - label_month is an EARLIER month than exec_month: label-based
         logic is simply more conservative/stale than necessary -- a
         lag, not a correctness problem.
  6. Explicit focus on "first trading week following month-end": for
     every month-end date, find the weekly bar whose execution window
     contains the first trading day after that month-end, and check that
     bar specifically.

Usage (run locally -- needs yfinance):
    python3 milestone_a_signal_timing_check.py --tickers AAPL,MSFT,CAT,FAST
    python3 milestone_a_signal_timing_check.py
        # full bt.UNIVERSE + ETF basket
"""
import argparse
import sys
import numpy as np
import pandas as pd
import backtest_33fvb as bt
from milestone_a_engine_fix import prep_v2, ETF_BASKET


def bar_windows(weekly_index, daily_index):
    """Real trading days belonging to each weekly bar: [label_i, label_{i+1}),
    determined from the actual data, not assumed from the label's weekday."""
    idx = list(weekly_index)
    out = {}
    for i, lab in enumerate(idx):
        upper = idx[i + 1] if i + 1 < len(idx) else lab + pd.Timedelta(days=7)
        days = daily_index[(daily_index >= lab) & (daily_index < upper)]
        out[lab] = days
    return out


def latest_leq(index, cutoff, strict=False):
    if strict:
        sub = index[index < cutoff]
    else:
        sub = index[index <= cutoff]
    return sub.max() if len(sub) else None


def check_ticker(tk):
    weekly = bt.fetch(tk)
    daily = bt.fetch_daily(tk)
    if weekly is None or daily is None or len(weekly) < bt.FVB_LEN * 5:
        return None, f"{tk}: insufficient data"

    _, monthly = prep_v2(weekly, daily)  # monthly index = real month-end dates
    windows = bar_windows(weekly.index, daily.index)

    rows = []
    for lab in weekly.index:
        days = windows.get(lab)
        if days is None or len(days) == 0:
            continue
        exec_date = days.min()
        last_date = days.max()
        label_month = latest_leq(monthly.index, lab, strict=False)
        exec_month = latest_leq(monthly.index, exec_date, strict=True)
        if label_month is None and exec_month is None:
            continue
        if label_month != exec_month:
            direction = "LOOKAHEAD-RISK" if (label_month is not None and
                        (exec_month is None or label_month > exec_month)) else "LAG-ONLY"
            rows.append({
                "ticker": tk, "weekly_label": lab, "exec_date": exec_date,
                "last_trading_date": last_date, "label_month": label_month,
                "exec_month": exec_month, "direction": direction,
            })

    # explicit focus: first trading week after each month-end
    month_end_focus = []
    for me in monthly.index:
        after = daily.index[daily.index > me]
        if len(after) == 0:
            continue
        first_trading_day_after = after.min()
        # which weekly bar's window contains this day?
        for lab, days in windows.items():
            if len(days) and days.min() <= first_trading_day_after <= days.max():
                label_month = latest_leq(monthly.index, lab, strict=False)
                exec_month = latest_leq(monthly.index, first_trading_day_after, strict=True)
                month_end_focus.append({
                    "ticker": tk, "month_end": me,
                    "first_trading_day_after_month_end": first_trading_day_after,
                    "containing_weekly_label": lab,
                    "label_month": label_month, "exec_month": exec_month,
                    "match": label_month == exec_month,
                })
                break

    return (pd.DataFrame(rows), pd.DataFrame(month_end_focus)), None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", type=str, default=None)
    ap.add_argument("--no-etfs", action="store_true")
    args = ap.parse_args()

    tickers = args.tickers.split(",") if args.tickers else list(bt.UNIVERSE)
    all_syms = [(t, "stock") for t in tickers]
    if not args.no_etfs and not args.tickers:
        all_syms += [(t, "etf") for t in ETF_BASKET]

    all_disc = []
    all_focus = []
    for i, (tk, uni) in enumerate(all_syms):
        result, err = check_ticker(tk)
        if err:
            print(err, file=sys.stderr)
            continue
        disc, focus = result
        n_lookahead = (disc["direction"] == "LOOKAHEAD-RISK").sum() if len(disc) else 0
        n_lag = (disc["direction"] == "LAG-ONLY").sum() if len(disc) else 0
        n_focus_mismatch = (~focus["match"]).sum() if len(focus) else 0
        print(f"[{i+1}/{len(all_syms)}] {tk} ({uni}): "
              f"{len(disc)} total discrepancies ({n_lookahead} lookahead-risk, {n_lag} lag-only); "
              f"{n_focus_mismatch}/{len(focus)} first-week-after-month-end bars mismatched",
              file=sys.stderr)
        if len(disc):
            disc.insert(0, "universe", uni)
            all_disc.append(disc)
        if len(focus):
            focus.insert(0, "universe", uni)
            all_focus.append(focus)

    disc_all = pd.concat(all_disc, ignore_index=True) if all_disc else pd.DataFrame()
    focus_all = pd.concat(all_focus, ignore_index=True) if all_focus else pd.DataFrame()

    disc_all.to_csv("milestone_a_signal_timing_discrepancies.csv", index=False)
    focus_all.to_csv("milestone_a_month_end_focus.csv", index=False)

    pd.set_option("display.width", 200)
    pd.set_option("display.max_rows", 200)

    print("\n=== SIGNAL-TIMING DISCREPANCIES (label-based cutoff vs real-execution-date cutoff) ===")
    print(f"Total bars with any discrepancy: {len(disc_all)}")
    if len(disc_all):
        print(disc_all["direction"].value_counts().to_string())
        print("\nAny LOOKAHEAD-RISK rows (label-based logic would have used a month before it "
              "was truly knowable as of the real execution start):")
        lr = disc_all[disc_all["direction"] == "LOOKAHEAD-RISK"]
        print(lr.to_string(index=False) if len(lr) else "  none")
    else:
        print("  none found -- label-based and execution-date-based cutoffs always agreed.")

    print("\n=== FIRST TRADING WEEK AFTER EACH MONTH-END: label-month vs exec-month ===")
    print(f"Total month-ends checked: {len(focus_all)}, mismatches: "
          f"{(~focus_all['match']).sum() if len(focus_all) else 0}")
    if len(focus_all):
        mism = focus_all[~focus_all["match"]]
        print(mism.to_string(index=False) if len(mism) else "  none -- always matched.")

    print(f"\nSaved: milestone_a_signal_timing_discrepancies.csv ({len(disc_all)} rows)")
    print(f"Saved: milestone_a_month_end_focus.csv ({len(focus_all)} rows)")


if __name__ == "__main__":
    main()
