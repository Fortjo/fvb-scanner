#!/usr/bin/env python3
"""
One-off check, triggered from a chat conversation: Peter's 2026-11-23 email
("18 out of 20 times, the S&P bounced here") claims that across SPY's full
history (ETF went live Jan 1993), price has pulled back into the monthly
Smart Money Band 20 times, bounced 18 of them, and broken only twice (2000
and 2008).

This reruns that exact claim through OUR OWN monthly FVB/state calculation
(backtest_33fvb._compute_real_fvb -- same band math + sticky green/red state
used for every live trade decision) on SPY's full available monthly history,
independent of Peter's own chart. Point: see whether our band construction
produces a similar historical touch/bounce/break count on the single most
scrutinized ticker (SPY) -- a sanity check on calibration, not a new trading
signal. Not wired into any live script; run manually, print-only.

Methodology (closest reasonable reading of his email, since he doesn't give
an exact mechanical rule): a "pullback into the band" episode starts the
first month price (the monthly Low) enters the band range [lower, upper]
after a run of months fully above it. The episode is scored:
  BOUNCE -- state (our sticky green/red trend flag) never turns red during
            the episode or in the 12 months after it starts
  BREAK  -- state turns red during the episode or within that 12-month window
Episodes are deduplicated: once scored, we don't start a new episode until
price has gone back above the band and pulled in again.
"""
from __future__ import annotations
import sys
import pandas as pd
import numpy as np
from backtest_33fvb import _compute_real_fvb, FVB_LEN

def fetch_full_history(ticker: str) -> pd.DataFrame:
    import yfinance as yf
    df = yf.download(ticker, start="1990-01-01", interval="1d",
                      auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df[["Open", "High", "Low", "Close"]].dropna()

def main(ticker="SPY"):
    daily = fetch_full_history(ticker)
    print(f"{ticker}: {len(daily)} daily bars, {daily.index[0].date()} -> {daily.index[-1].date()}")

    m = daily.resample("ME").agg({"Open": "first", "High": "max",
                                   "Low": "min", "Close": "last"}).dropna()
    green, upper, lower = _compute_real_fvb(m, FVB_LEN)

    valid = (~upper.isna()) & (~lower.isna())
    idx = m.index[valid]
    low = m["Low"][valid]
    up = upper[valid]
    lo = lower[valid]
    st = green[valid]

    print(f"Band valid from {idx[0].date()} ({valid.sum()} of {len(m)} months have a computed band)\n")

    episodes = []
    in_episode = False
    episode_start = None
    for i in range(len(idx)):
        inside_or_below = low.iloc[i] <= up.iloc[i]
        if inside_or_below and not in_episode:
            in_episode = True
            episode_start = i
        elif not inside_or_below and in_episode:
            in_episode = False

        if in_episode and episode_start == i:
            # just started -- look ahead up to 12 months for a red flip
            window_end = min(i + 13, len(idx))
            broke = (~st.iloc[i:window_end]).any()
            episodes.append({
                "date": idx[i].date(),
                "low": round(float(low.iloc[i]), 2),
                "lower_band": round(float(lo.iloc[i]), 2),
                "upper_band": round(float(up.iloc[i]), 2),
                "outcome": "BREAK" if broke else "BOUNCE",
            })

    n_bounce = sum(1 for e in episodes if e["outcome"] == "BOUNCE")
    n_break = sum(1 for e in episodes if e["outcome"] == "BREAK")
    print(f"=== {ticker}: {len(episodes)} pullback episodes, "
          f"{n_bounce} bounced, {n_break} broke ===\n")
    for e in episodes:
        print(f"  {e['date']}  low={e['low']:<10} band=[{e['lower_band']}, {e['upper_band']}]  -> {e['outcome']}")

    print(f"\nPeter's claim (his chart, SPY since 1993 ETF inception): "
          f"20 episodes, 18 bounced, 2 broke (2000, 2008).")

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "SPY")
