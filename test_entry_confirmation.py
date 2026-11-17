#!/usr/bin/env python3
"""
Tests two cheaper, more SMC-native "is this touch real" confirmation ideas
against the existing trades, now that StochRSI (test_stochrsi_filter.py,
test_stochrsi_variants.py, test_stochrsi_magnitude.py) came up empty in
every framing tried (require oversold-turning-up, avoid-if-oversold wired
into the real portfolio engine, and bounce-magnitude/holding-period).

Both ideas use the ENTRY WEEK'S OWN bar -- not a lagged prior bar. This is
NOT lookahead: the core engine's own entry rule (green[i], low[i] <=
band_px, prior close > prior_band_px) already decides on this exact bar's
own price action, known at that week's close. Measuring how convincingly
that same bar rejected the zone is just reading the same bar more closely,
not peeking into the future.

IDEA 1 -- candle rejection strength (price action only, from the weekly
OHLC we already fetch for every trade, no new data source):
  close_position = (close - low) / (high - low)
      1.0 = closed at the week's high (strongest possible rejection)
      0.0 = closed at the week's low (no rejection at all -- note the core
      engine already requires green+low<=band+prior close>prior band, so
      this will never be literally 0, but it varies)
  lower_wick_ratio = (min(open, close) - low) / (high - low)
      how much of the bar's range is "wick below the real body" -- a
      classic SMC/ICT rejection tell (long wick = sellers pushed it down,
      buyers overwhelmed them within the same bar)

IDEA 2 -- volume spike on the touch bar (needs a separate fetch that
keeps Volume -- backtest_33fvb.py's own fetch() drops it):
  vol_ratio = entry week's volume / trailing 10-week average volume
      (the 10-week average is computed over the 10 weeks BEFORE the entry
      week, never including it, so this stays no-lookahead even though
      the numerator is the entry week's own volume)

For each metric: splits trades into quartiles and reports win rate, PF,
and expectancy per quartile, the same style as test_stochrsi_variants.py's
quintile breakdown -- a real "stronger signal = better trade" relationship
should show up as a clean trend across quartiles.

Needs network (yfinance) -- run on a machine with real network access.

Usage:
    python3 test_entry_confirmation.py milestone_c_sp500_baseline_trades.csv
"""
from __future__ import annotations
import sys
from typing import Optional
import numpy as np
import pandas as pd
from test_stochrsi_filter import bucket_stats
from test_stochrsi_variants import entry_week_index

START = "1970-01-01"


def fetch_ohlcv(ticker: str) -> Optional[pd.DataFrame]:
    """Same as backtest_33fvb.fetch(), but keeps Volume."""
    import yfinance as yf
    df = yf.download(ticker, start=START, interval="1wk",
                      auto_adjust=True, progress=False)
    if df is None or len(df) < 50:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df[["Open", "High", "Low", "Close", "Volume"]].dropna()


def candle_metrics(weekly: pd.DataFrame, i: int) -> tuple[float, float]:
    if i < 0 or i >= len(weekly):
        return None, None
    o, h, l, c = weekly["Open"].iloc[i], weekly["High"].iloc[i], weekly["Low"].iloc[i], weekly["Close"].iloc[i]
    rng = h - l
    if rng <= 0 or pd.isna(rng):
        return None, None
    close_position = (c - l) / rng
    lower_wick_ratio = (min(o, c) - l) / rng
    return float(close_position), float(lower_wick_ratio)


def volume_ratio(weekly: pd.DataFrame, i: int, lookback: int = 10) -> float:
    if i < lookback or i >= len(weekly):
        return None
    baseline = weekly["Volume"].iloc[i - lookback:i].mean()
    if pd.isna(baseline) or baseline <= 0:
        return None
    v = weekly["Volume"].iloc[i]
    if pd.isna(v):
        return None
    return float(v / baseline)


def fetch_metrics(trades: pd.DataFrame) -> pd.DataFrame:
    trades = trades.copy()
    tickers = sorted(trades["ticker"].unique())
    print(f"{len(trades)} trades across {len(tickers)} tickers. Fetching weekly OHLCV...", file=sys.stderr)

    cp_by_idx, lwr_by_idx, vr_by_idx = {}, {}, {}
    for n, tk in enumerate(tickers, 1):
        sub = trades[trades["ticker"] == tk]
        try:
            weekly = fetch_ohlcv(tk)
        except Exception as e:
            print(f"{tk}: fetch error {e}", file=sys.stderr)
            weekly = None
        if weekly is None:
            print(f"{tk}: insufficient data, leaving {len(sub)} trade(s) unflagged", file=sys.stderr)
            for idx in sub.index:
                cp_by_idx[idx] = None
                lwr_by_idx[idx] = None
                vr_by_idx[idx] = None
            continue
        for idx, row in sub.iterrows():
            entry_week_i, _ = entry_week_index(weekly.index, row["entry_date"])
            cp, lwr = candle_metrics(weekly, entry_week_i)
            vr = volume_ratio(weekly, entry_week_i)
            cp_by_idx[idx] = cp
            lwr_by_idx[idx] = lwr
            vr_by_idx[idx] = vr
        print(f"[{n}/{len(tickers)}] {tk}: done", file=sys.stderr)

    trades["close_position"] = trades.index.map(cp_by_idx.get)
    trades["lower_wick_ratio"] = trades.index.map(lwr_by_idx.get)
    trades["vol_ratio"] = trades.index.map(vr_by_idx.get)
    return trades


def quartile_report(trades: pd.DataFrame, col: str, ret_col: str, label: str):
    valid = trades[trades[col].notna()].copy()
    print(f"\n=== {label} ({col}) ===  {len(valid)}/{len(trades)} trades matched")
    if len(valid) < 8:
        print("  not enough matched trades for a quartile breakdown")
        return
    valid["q"] = pd.qcut(valid[col], 4, duplicates="drop")
    for q, grp in valid.groupby("q", observed=True):
        s = bucket_stats(grp[ret_col].values)
        print(f"  {col} in {q}: {s['trades']:4d} trades, win {s['win_rate']:.1f}%, "
              f"PF {s['pf']:.2f}, exp {s['expectancy']:+.2f}%")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 test_entry_confirmation.py <trades_csv>", file=sys.stderr)
        sys.exit(1)
    trades_path = sys.argv[1]
    trades = pd.read_csv(trades_path, parse_dates=["entry_date", "exit_date"])
    ret_col = "net_ret_pct" if "net_ret_pct" in trades.columns else "ret_pct"
    if ret_col not in trades.columns:
        print(f"Couldn't find a return column in {trades_path}. Columns: {list(trades.columns)}",
              file=sys.stderr)
        sys.exit(1)

    trades = fetch_metrics(trades)

    quartile_report(trades, "close_position", ret_col,
                     "IDEA 1a: candle closed near its high (strong rejection) vs near its low")
    quartile_report(trades, "lower_wick_ratio", ret_col,
                     "IDEA 1b: long lower wick (rejection wick) vs short/none")
    quartile_report(trades, "vol_ratio", ret_col,
                     "IDEA 2: volume spike on the touch bar vs normal/quiet volume")

    out_path = trades_path.rsplit(".", 1)[0] + "_with_confirmation.csv"
    trades.to_csv(out_path, index=False)
    print(f"\nSaved: {out_path}")
    print("\nRead each as a trend across quartiles: a real confirmation signal should show win "
          "rate/expectancy climbing (or falling) smoothly from the weakest to the strongest quartile. "
          "Flat or noisy across quartiles means that metric isn't adding timing information either.")


if __name__ == "__main__":
    main()
