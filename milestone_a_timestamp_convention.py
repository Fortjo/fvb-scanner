#!/usr/bin/env python3
"""
Empirically verify yfinance's weekly-bar timestamp convention -- NOT
assumed from memory or documentation, actually checked against real daily
bars. This matters for Milestone A/B because "when does a monthly
indicator become available relative to a weekly entry/exit" depends on
whether a weekly bar's index label is the START of its trading week
(Monday) or the END (the last trading day, usually Friday), and on
exactly which day's Close the weekly bar's own Close reflects.

Checks four specific situations, not just a generic sample:
  1. A normal 5-trading-day week, no holidays.
  2. A week containing a US market holiday (e.g. Thanksgiving or
     Independence Day), where the index label's weekday convention
     could plausibly shift.
  3. A week whose Friday falls on a market holiday (so the week's last
     trading day is Thursday) -- does the weekly label still say Friday?
  4. A week whose 5 trading days span two different calendar months
     (the exact situation Milestone A's fix #5 targets) -- which month's
     data actually appears in the weekly bar's High/Low?

Must run locally -- needs yfinance.

    python3 milestone_a_timestamp_convention.py --ticker AAPL
"""
import argparse
import pandas as pd


def fetch_weekly(ticker, start, end=None):
    import yfinance as yf
    df = yf.download(ticker, start=start, end=end, interval="1wk",
                      auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df[["Open", "High", "Low", "Close"]].dropna()


def fetch_daily(ticker, start, end=None):
    import yfinance as yf
    df = yf.download(ticker, start=start, end=end, interval="1d",
                      auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df[["Open", "High", "Low", "Close"]].dropna()


def analyze_week(weekly_row_date, weekly_row, daily):
    """For one weekly bar, find which daily bars plausibly belong to it
    (the 5 calendar days up to and including the label date, and
    separately the 5 days starting from the label date) and report which
    grouping's Open/High/Low/Close actually matches the weekly bar --
    this is the empirical test, not an assumption."""
    label = weekly_row_date
    # candidate A: label is the WEEK END -- days in (label - 6d, label]
    window_end = daily[(daily.index > label - pd.Timedelta(days=6)) & (daily.index <= label)]
    # candidate B: label is the WEEK START -- days in [label, label + 6d)
    window_start = daily[(daily.index >= label) & (daily.index < label + pd.Timedelta(days=6))]

    def matches(window):
        if window.empty:
            return False, None
        o = abs(window["Open"].iloc[0] - weekly_row["Open"]) < 0.02
        c = abs(window["Close"].iloc[-1] - weekly_row["Close"]) < 0.02
        h = abs(window["High"].max() - weekly_row["High"]) < 0.02
        l = abs(window["Low"].min() - weekly_row["Low"]) < 0.02
        return (o and c and h and l), window

    end_match, end_win = matches(window_end)
    start_match, start_win = matches(window_start)
    return end_match, start_match, window_end, window_start


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", type=str, default="AAPL")
    args = ap.parse_args()
    tk = args.ticker

    print(f"=== yfinance weekly-bar timestamp convention check: {tk} ===\n")

    # 1. Normal week (pick a quiet mid-year week)
    weekly = fetch_weekly(tk, "2023-01-01", "2023-03-01")
    daily = fetch_daily(tk, "2022-12-20", "2023-03-05")
    print("--- Case 1: normal week ---")
    for d in weekly.index[1:3]:
        end_m, start_m, we, ws = analyze_week(d, weekly.loc[d], daily)
        print(f"  weekly label {d.date()} ({d.day_name()}): "
              f"matches WEEK-END window = {end_m}, matches WEEK-START window = {start_m}")
        print(f"    week-end window days: {[x.date() for x in we.index]}")
        print(f"    week-start window days: {[x.date() for x in ws.index]}")

    # 2. Week containing a US holiday -- Thanksgiving week (4th Thursday Nov)
    weekly2 = fetch_weekly(tk, "2023-11-15", "2023-12-05")
    daily2 = fetch_daily(tk, "2023-11-13", "2023-12-05")
    print("\n--- Case 2: Thanksgiving week (holiday Thu Nov 23, 2023) ---")
    for d in weekly2.index:
        if d.month == 11 or (d.month == 12 and d.day <= 3):
            end_m, start_m, we, ws = analyze_week(d, weekly2.loc[d], daily2)
            print(f"  weekly label {d.date()} ({d.day_name()}): "
                  f"END-match={end_m}, START-match={start_m}, "
                  f"week-end window days: {[x.date() for x in we.index]}")

    # 3. A week whose FRIDAY is itself a market holiday -- Independence Day
    #    week: July 4 2023 was a Tuesday, so instead check a week where
    #    Friday IS the holiday -- e.g. Good Friday week. 2023 Good Friday
    #    was April 7 (a Friday) -- market closed that day.
    weekly3 = fetch_weekly(tk, "2023-03-28", "2023-04-15")
    daily3 = fetch_daily(tk, "2023-03-27", "2023-04-14")
    print("\n--- Case 3: week where Friday is a market holiday (Good Friday, Apr 7 2023) ---")
    for d in weekly3.index:
        end_m, start_m, we, ws = analyze_week(d, weekly3.loc[d], daily3)
        print(f"  weekly label {d.date()} ({d.day_name()}): "
              f"END-match={end_m}, START-match={start_m}, "
              f"week-end window days: {[x.date() for x in we.index]}")

    # 4. A week whose trading days span two different calendar months
    print("\n--- Case 4: week spanning a month boundary ---")
    for d in weekly.index:
        end_m, start_m, we, ws = analyze_week(d, weekly.loc[d], daily)
        days_in_win = we if end_m else ws
        if len(days_in_win) and days_in_win.index.month.nunique() > 1:
            print(f"  weekly label {d.date()} ({d.day_name()}): spans months "
                  f"{sorted(set(days_in_win.index.month))}")
            print(f"    days actually in this week: {[x.date() for x in days_in_win.index]}")
            print(f"    weekly High={weekly.loc[d,'High']:.2f}  Low={weekly.loc[d,'Low']:.2f}  "
                  f"Close={weekly.loc[d,'Close']:.2f}")
            # which calendar month does the High/Low actually come from?
            if end_m or start_m:
                hi_day = days_in_win["High"].idxmax()
                lo_day = days_in_win["Low"].idxmin()
                print(f"    High actually occurred on {hi_day.date()} (month {hi_day.month}), "
                      f"Low on {lo_day.date()} (month {lo_day.month})")

    print("\n=== CONCLUSION ===")
    print("If 'matches WEEK-END window' is consistently True and 'WEEK-START' consistently")
    print("False (or vice versa) across all four cases, that settles the convention. If Case 3")
    print("shows the holiday-shortened week's label still falls on the usual weekday (not shifted")
    print("to Thursday), that confirms yfinance labels by a FIXED calendar anchor, not 'whichever")
    print("day the week actually ended.' Case 4's output is the direct evidence for whether a")
    print("month-boundary-spanning week's High/Low can include the FOLLOWING month's price action")
    print("-- exactly the condition Milestone A's fix #5 targets.")
    print("\nSend this full output back -- do not summarize it, the auditor needs the raw dates.")


if __name__ == "__main__":
    main()
