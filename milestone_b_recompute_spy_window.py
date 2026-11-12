#!/usr/bin/env python3
"""
One-off helper: recompute the SPY-comparable window from the
milestone_b_daily_equity.csv / milestone_b_trades.csv you already saved
from a prior `python3 milestone_b_portfolio_sim.py` run -- no re-fetch,
no re-simulation. This is the fix for the date-range bug found after
reviewing the first run's output (strategy start_date 1970 vs SPY
start_date 1993 -- two different windows were being compared).

Usage:
    python3 milestone_b_recompute_spy_window.py
"""
import pandas as pd
import milestone_b_portfolio_sim as mb

daily = pd.read_csv("milestone_b_daily_equity.csv", index_col=0, parse_dates=True)
trades = pd.read_csv("milestone_b_trades.csv", parse_dates=["entry_date", "exit_date"])

if "spy_equity" not in daily.columns:
    raise SystemExit("milestone_b_daily_equity.csv has no spy_equity column -- "
                      "re-run milestone_b_portfolio_sim.py (pulled latest) to regenerate it.")

spy = daily["spy_equity"].dropna()
if len(spy) == 0:
    raise SystemExit("spy_equity column is entirely empty -- SPY fetch must have failed "
                      "in the original run.")

common_start = max(daily.index.min(), spy.index.min())
common_end = min(daily.index.max(), spy.index.max())
print(f"SPY-comparable window (both sides, identical dates): "
      f"{common_start.date()} to {common_end.date()}")

daily_window = daily.loc[(daily.index >= common_start) & (daily.index <= common_end)]
spy_window = pd.DataFrame({"equity": spy.loc[(spy.index >= common_start) & (spy.index <= common_end)]})
trades_window = trades[(trades["exit_date"] >= common_start) & (trades["exit_date"] <= common_end)]

metrics_full = mb.compute_metrics(daily, trades, mb.START_CAPITAL)
metrics_window = mb.compute_metrics(daily_window, trades_window, daily_window["equity"].iloc[0])
spy_metrics = mb.compute_metrics(spy_window.assign(open_positions=1), pd.DataFrame(), mb.START_CAPITAL)

rows = [{"metric": k, "strategy_full_history": metrics_full.get(k),
         "strategy_vs_spy_window": metrics_window.get(k),
         "spy_buy_and_hold_same_window": spy_metrics.get(k)}
        for k in metrics_full.keys()]
out = pd.DataFrame(rows)
out.to_csv("milestone_b_summary_corrected.csv", index=False)
pd.set_option("display.width", 160)
print(out.to_string(index=False))
print("\nSaved: milestone_b_summary_corrected.csv")
