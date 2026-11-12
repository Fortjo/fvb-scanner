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
# NOTE: daily_window["equity"].iloc[0] is the ACTUAL dollar level the
# strategy had already compounded to by common_start (it had been
# running since 1970) -- NOT $100k. CAGR/Sharpe/Sortino/drawdown/
# volatility are scale-invariant so this basis doesn't affect them, but
# raw dollar figures (final_equity) computed on it are NOT on the same
# starting-capital basis as SPY's. Reported separately below.
metrics_window = mb.compute_metrics(daily_window, trades_window, daily_window["equity"].iloc[0])
spy_metrics = mb.compute_metrics(spy_window.assign(open_positions=1), pd.DataFrame(), mb.START_CAPITAL)

# Illustrative-only: what the SAME equity curve (same trades, same
# period) would be worth if it had been rescaled to start at exactly
# $100,000 on common_start, for a dollar-for-dollar reading next to
# SPY's figure. This is a pure linear rescale of the realized equity
# path, NOT a re-simulation with smaller capital -- real position
# sizing/skips (max_positions, insufficient_cash) would likely differ
# slightly with a genuinely smaller capital base from the start, so
# treat this as illustrative, not as a rerun of the strategy.
rescale = mb.START_CAPITAL / daily_window["equity"].iloc[0]
final_equity_rescaled_100k = daily_window["equity"].iloc[-1] * rescale

rows = [{"metric": k, "strategy_full_history": metrics_full.get(k),
         "strategy_vs_spy_window": metrics_window.get(k),
         "spy_buy_and_hold_same_window": spy_metrics.get(k)}
        for k in metrics_full.keys()]
rows.append({"metric": "final_equity_rescaled_to_100k_start (illustrative only)",
             "strategy_full_history": None,
             "strategy_vs_spy_window": final_equity_rescaled_100k,
             "spy_buy_and_hold_same_window": spy_metrics.get("final_equity")})
out = pd.DataFrame(rows)
out.to_csv("milestone_b_summary_corrected.csv", index=False)
pd.set_option("display.width", 160)
print(out.to_string(index=False))
print("\nSaved: milestone_b_summary_corrected.csv")
