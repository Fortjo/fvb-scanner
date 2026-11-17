#!/usr/bin/env python3
"""
Weekly zone + daily check -- one timeframe level down from the validated
strategy, Forty's idea (mirrors how Peter's own "Silver Bullet" concept
uses a higher-timeframe zone with a lower-timeframe trigger):

  Validated baseline:  MONTHLY zone, checked on WEEKLY bars.
  This prototype:       WEEKLY zone, checked on DAILY bars.

Does NOT touch backtest_33fvb.py -- this is a separate, standalone
prototype so the validated baseline is completely unaffected. Reuses
backtest_33fvb.run() UNCHANGED: that function only needs the right
columns (basis/upperR/lowerB/green/pivot_conf/prev_hh) at whatever bar
frequency it's given, so the only new code needed is a daily-granularity
version of prep().

PARAMETER CONVERSION -- same period COUNTS as the validated strategy,
converted to the new native unit by real elapsed time, not reused as the
same raw digit on a different clock (reusing "33" and "200" unchanged
would silently test a completely different, much shorter-term strategy
at the same time as the actual question -- "does checking the zone
daily instead of weekly help" -- which would confound two unrelated
changes):
    FVB_LEN (33 months)        -> ~143 weeks   (33 * ~4.345 weeks/month)
    median window (1000 months)-> ~4345 weeks  (same conversion, this is
                                                 _compute_real_fvb's band-
                                                 width lookback, hardcoded
                                                 at 1000 in backtest_33fvb
                                                 so it's duplicated here
                                                 with a configurable window)
    NBAR_LEN (200 weeks)       -> 1000 trading days (200 * 5 days/week)
    PIV_L, PIV_R (5 weeks each)-> 25 trading days each

Needs network (yfinance, via backtest_33fvb.fetch_daily) -- run on a
machine with real network access.

Usage:
    python3 test_weekly_zone_daily_check.py --tickers MSFT,AAPL,ORCL,SOFI
    python3 test_weekly_zone_daily_check.py --synthetic   # offline smoke test
"""
from __future__ import annotations
import argparse
import sys
from collections import deque
from typing import Optional
import numpy as np
import pandas as pd
import backtest_33fvb as bt

WEEKS_PER_MONTH = 365.25 / 7 / 12   # ~4.345
DAYS_PER_WEEK = 5                  # trading days

FVB_LEN_W = round(bt.FVB_LEN * WEEKS_PER_MONTH)          # 33 months  -> ~143 weeks
MEDIAN_WINDOW_W = round(1000 * WEEKS_PER_MONTH)          # 1000 months -> ~4345 weeks
NBAR_LEN_D = bt.NBAR_LEN * DAYS_PER_WEEK                 # 200 weeks  -> 1000 trading days
PIV_L_D = bt.PIV_L * DAYS_PER_WEEK                       # 5 weeks    -> 25 trading days
PIV_R_D = bt.PIV_R * DAYS_PER_WEEK                       # 5 weeks    -> 25 trading days


def _compute_real_fvb_param(m: pd.DataFrame, length: int, median_window: int):
    """Same algorithm as backtest_33fvb._compute_real_fvb(), duplicated
    only because that function hardcodes its median window at 1000 bars
    -- fine when the input is monthly (1000 months), wrong when the
    input is weekly (would silently become a ~19-year window instead of
    the intended ~83-year-equivalent ~4345 weeks). This version takes
    the window as a parameter so the conversion above is honored."""
    ohlc4 = (m["Open"] + m["High"] + m["Low"] + m["Close"]) / 4
    basis = ohlc4.rolling(length).mean()
    low_spread = m["Low"] / basis
    high_spread = m["High"] / basis
    straddle = (m["Low"] < basis) & (m["High"] > basis)

    n = len(m)
    dev_up = np.where(straddle, high_spread, np.nan)
    dev_down = np.where(straddle, low_spread, np.nan)

    median_up = np.full(n, np.nan)
    median_down = np.full(n, np.nan)
    up_win, down_win = deque(maxlen=median_window), deque(maxlen=median_window)
    for i in range(n):
        up_win.append(dev_up[i])
        down_win.append(dev_down[i])
        vu = [x for x in up_win if not np.isnan(x)]
        vd = [x for x in down_win if not np.isnan(x)]
        if vu:
            median_up[i] = np.median(vu)
        if vd:
            median_down[i] = np.median(vd)

    upper_band = basis.values * median_up
    lower_band = basis.values * median_down

    dir_switch = np.zeros(n, dtype=int)
    state = 0
    hi, lo = m["High"].values, m["Low"].values
    for i in range(n):
        if not np.isnan(lower_band[i]) and hi[i] < lower_band[i]:
            state = -1
        elif not np.isnan(upper_band[i]) and lo[i] > upper_band[i]:
            state = 1
        dir_switch[i] = state

    green = pd.Series(dir_switch == 1, index=m.index)
    upper = pd.Series(upper_band, index=m.index)
    lower = pd.Series(lower_band, index=m.index)
    return green, upper, lower


def prep_weekly_zone_daily_check(df: pd.DataFrame) -> pd.DataFrame:
    """Weekly-zone analog of backtest_33fvb.prep()'s real_cross branch,
    fed a DAILY df instead of a weekly one. The zone (basis/bands/green
    state) is computed from WEEKLY bars, shift(1) to use only the last
    FULLY COMPLETED week (no lookahead -- same convention as the
    validated strategy's monthly shift(1)), then forward-filled onto
    every day of the following week. The CHECK (does today's low touch
    the band, was yesterday's close already above it) happens every
    day -- that's the only thing this changes relative to the baseline."""
    m = df.resample("W-FRI").agg({"Open": "first", "High": "max",
                                   "Low": "min", "Close": "last"}).dropna()
    ohlc4 = (m["Open"] + m["High"] + m["Low"] + m["Close"]) / 4
    basis = ohlc4.rolling(FVB_LEN_W).mean()
    green, upperR, lowerR = _compute_real_fvb_param(m, FVB_LEN_W, MEDIAN_WINDOW_W)
    weekly = pd.DataFrame({"basis": basis, "upperR": upperR, "lowerR": lowerR,
                            "green": green}).shift(1)
    out = df.copy()
    ww = weekly.reindex(df.index, method="ffill")
    out[["basis", "upperR", "lowerR", "green"]] = ww
    out["halfw"] = out["upperR"] - out["basis"]
    out["lowerB"] = out["lowerR"]

    h = df["High"].values
    n = len(h)
    conf = np.full(n, np.nan)
    for i in range(PIV_L_D, n - PIV_R_D):
        w = h[i - PIV_L_D: i + PIV_R_D + 1]
        if h[i] == w.max() and (w == h[i]).sum() == 1:
            conf[i + PIV_R_D] = h[i]
    out["pivot_conf"] = conf
    out["prev_hh"] = df["High"].rolling(NBAR_LEN_D).max().shift(1)
    return out.dropna(subset=["basis"])


def synthetic_daily(ticker: str, n: int = 7500, seed: Optional[int] = None) -> pd.DataFrame:
    """Daily analog of backtest_33fvb.synthetic(), for offline smoke
    testing only -- not used for any real comparison. n=7500 trading
    days (~30 years) by default, long enough to clear the ~4345-week
    median warmup after resampling to weekly."""
    rng = np.random.default_rng(abs(hash(ticker)) % 2**32 if seed is None else seed)
    rets = rng.normal(0.0004, 0.016, n)   # roughly daily-scaled version of synthetic()'s weekly drift/vol
    close = 50 * np.exp(np.cumsum(rets))
    open_ = close * (1 + rng.normal(0, 0.004, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.006, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.006, n)))
    idx = pd.bdate_range("2000-01-03", periods=n)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close}, index=idx)


def compare_ticker(tk: str, synthetic: bool = False) -> Optional[dict]:
    """Runs BOTH the validated weekly/monthly baseline and this weekly-
    zone/daily-check prototype on the SAME ticker, same High band +
    NBarHigh exit (the strategy's own best-performing combo), and
    returns both stat dicts side by side."""
    if synthetic:
        daily = synthetic_daily(tk)
        weekly_raw = daily.resample("W-FRI").agg({"Open": "first", "High": "max",
                                                    "Low": "min", "Close": "last"}).dropna()
    else:
        try:
            daily = bt.fetch_daily(tk)
            weekly_raw = bt.fetch(tk)
        except Exception as e:
            print(f"{tk}: fetch error {e}", file=sys.stderr)
            return None
    if daily is None or weekly_raw is None or len(daily) < 100 or len(weekly_raw) < bt.FVB_LEN * 5:
        print(f"{tk}: insufficient data", file=sys.stderr)
        return None

    base_df = bt.prep(weekly_raw)
    if len(base_df) < bt.NBAR_LEN:
        print(f"{tk}: too short after warmup (baseline)", file=sys.stderr)
        return None
    base_trades = bt.run(base_df, "High", "NBarHigh")
    base_stats = bt.stats(base_trades)

    cand_df = prep_weekly_zone_daily_check(daily)
    if len(cand_df) < NBAR_LEN_D:
        print(f"{tk}: too short after warmup (daily-check)", file=sys.stderr)
        return None
    cand_trades = bt.run(cand_df, "High", "NBarHigh")
    cand_stats = bt.stats(cand_trades)

    return {"ticker": tk,
            "baseline_trades": base_stats["trades"], "baseline_win_pct": base_stats["win_rate"],
            "baseline_pf": base_stats["pf"], "baseline_exp_pct": base_stats["expectancy"],
            "baseline_total_ret_pct": base_stats["total_ret"],
            "daily_check_trades": cand_stats["trades"], "daily_check_win_pct": cand_stats["win_rate"],
            "daily_check_pf": cand_stats["pf"], "daily_check_exp_pct": cand_stats["expectancy"],
            "daily_check_total_ret_pct": cand_stats["total_ret"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", type=str, default=None)
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args()

    print(f"FVB_LEN_W={FVB_LEN_W}  MEDIAN_WINDOW_W={MEDIAN_WINDOW_W}  "
          f"NBAR_LEN_D={NBAR_LEN_D}  PIV_L_D=PIV_R_D={PIV_L_D}\n", file=sys.stderr)

    if args.synthetic:
        tickers = ["SYN1", "SYN2", "SYN3"]
    else:
        tickers = args.tickers.split(",") if args.tickers else list(bt.UNIVERSE)

    rows = []
    for i, tk in enumerate(tickers, 1):
        r = compare_ticker(tk, synthetic=args.synthetic)
        if r is not None:
            rows.append(r)
        print(f"[{i}/{len(tickers)}] {tk}: done", file=sys.stderr)

    df = pd.DataFrame(rows)
    if df.empty:
        print("No tickers produced results.", file=sys.stderr)
        return
    pd.set_option("display.width", 160)
    print("\n=== PER-TICKER: baseline (monthly zone / weekly check) vs daily-check (weekly zone / daily check) ===")
    print(df.round(2).to_string(index=False))

    print("\n=== MEDIANS ACROSS TICKERS ===")
    med = df[["baseline_trades", "baseline_win_pct", "baseline_pf", "baseline_exp_pct", "baseline_total_ret_pct",
              "daily_check_trades", "daily_check_win_pct", "daily_check_pf", "daily_check_exp_pct",
              "daily_check_total_ret_pct"]].median()
    print(med.round(2).to_string())

    out_path = "weekly_zone_daily_check_comparison.csv"
    df.to_csv(out_path, index=False)
    print(f"\nSaved: {out_path}")
    print("\nThis is a per-ticker, uncapped-capital comparison -- same caveat as every other filter "
          "tested tonight applies in reverse here: MORE trades (from catching signals mid-week instead "
          "of waiting for the week to close) isn't automatically better, and a portfolio-level test "
          "(limited slots) would be the fair next step if this looks promising enough to pursue further.")


if __name__ == "__main__":
    main()
