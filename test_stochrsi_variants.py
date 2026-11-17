#!/usr/bin/env python3
"""
Broader StochRSI test -- several variants in ONE pass, against the pushback
that the first result (test_stochrsi_filter.py) might just be an artifact of
one specific, strict definition.

Reuses the same fetched weekly price history for every variant (no re-fetch
per variant) and reports all of them side by side, plus a continuous
decile breakdown, so a single binary cutoff isn't the only evidence either
way.

VARIANTS (all still no-lookahead -- see each docstring below):
  1. strict_cross_1wk   -- the original test: K crosses above D, exactly on
                            the last fully completed week before entry,
                            with min(K,D) < 20 on that cross.
  2. loose_cross_3wk    -- more forgiving on TIMING: a qualifying cross
                            happened any time in the 3 completed weeks
                            before entry, not necessarily the very last one.
  3. simple_oversold    -- no cross required at all: just "was %K under 20
                            on the last completed week", the plain oversold
                            reading by itself.
  4. entry_week_itself  -- same cross logic as variant 1, but one week LESS
                            conservative: uses the entry week's own close
                            instead of insisting on the prior week. Carries
                            a small theoretical lookahead risk (the entry
                            week's close isn't known until the week ends,
                            but the trade's real fill can happen any day
                            that week) -- included specifically to see how
                            much the conservative lag in variant 1 matters.
  5. continuous deciles -- bucket ALL trades by their variant-1 raw %K
                            value (not a 20-cutoff) into 5 bins and report
                            expectancy per bin, to check for a smooth
                            relationship a hard cutoff could be hiding or
                            fabricating.

Needs network (yfinance) -- run on a machine with real network access.

Usage:
    python3 test_stochrsi_variants.py milestone_c_sp500_baseline_trades.csv
"""
import sys
import numpy as np
import pandas as pd
import backtest_33fvb as bt
from test_stochrsi_filter import compute_stoch_rsi, bucket_stats, OVERSOLD


def entry_week_index(weekly_index, entry_date):
    """Returns (entry_week_i, i) where entry_week_i is the weekly bar whose
    week contains entry_date, and i = entry_week_i - 1 is the last fully
    completed week strictly before it (variant 1/2/3/5's reference point)."""
    pos = weekly_index.searchsorted(entry_date, side="right")
    entry_week_i = pos - 1
    return entry_week_i, entry_week_i - 1


def crossed_up_oversold(k, d, i) -> bool:
    if i < 1 or i >= len(k):
        return False
    vals = [k.iloc[i], k.iloc[i - 1], d.iloc[i], d.iloc[i - 1]]
    if any(pd.isna(v) for v in vals):
        return False
    crossed = k.iloc[i - 1] <= d.iloc[i - 1] and k.iloc[i] > d.iloc[i]
    return bool(crossed and min(vals) < OVERSOLD)


def any_cross_in_window(k, d, end_i, window) -> bool:
    """variant 2: True if crossed_up_oversold() is True for ANY bar in
    [end_i - window + 1, end_i], not just end_i itself."""
    for j in range(max(1, end_i - window + 1), end_i + 1):
        if crossed_up_oversold(k, d, j):
            return True
    return False


def simple_oversold(k, i) -> bool:
    if i < 0 or i >= len(k) or pd.isna(k.iloc[i]):
        return False
    return bool(k.iloc[i] < OVERSOLD)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 test_stochrsi_variants.py <trades_csv>", file=sys.stderr)
        sys.exit(1)
    trades_path = sys.argv[1]
    trades = pd.read_csv(trades_path, parse_dates=["entry_date", "exit_date"])
    ret_col = "net_ret_pct" if "net_ret_pct" in trades.columns else "ret_pct"
    if ret_col not in trades.columns:
        print(f"Couldn't find a return column in {trades_path}. Columns: {list(trades.columns)}",
              file=sys.stderr)
        sys.exit(1)

    tickers = sorted(trades["ticker"].unique())
    print(f"{len(trades)} trades across {len(tickers)} tickers. Fetching weekly price history once, "
          f"testing 5 variants per trade...", file=sys.stderr)

    cols = {
        "v1_strict_cross_1wk": {},
        "v2_loose_cross_3wk": {},
        "v3_simple_oversold": {},
        "v4_entry_week_itself": {},
        "v5_raw_k": {},
    }

    for n, tk in enumerate(tickers, 1):
        sub = trades[trades["ticker"] == tk]
        try:
            weekly = bt.fetch(tk)
        except Exception as e:
            print(f"{tk}: fetch error {e}", file=sys.stderr)
            weekly = None
        if weekly is None or len(weekly) < 50:
            print(f"{tk}: insufficient data, leaving {len(sub)} trade(s) unflagged", file=sys.stderr)
            for idx in sub.index:
                for c in cols:
                    cols[c][idx] = None
            continue
        k, d = compute_stoch_rsi(weekly["Close"])
        for idx, row in sub.iterrows():
            entry_week_i, i = entry_week_index(weekly.index, row["entry_date"])
            cols["v1_strict_cross_1wk"][idx] = crossed_up_oversold(k, d, i) if i >= 0 else False
            cols["v2_loose_cross_3wk"][idx] = any_cross_in_window(k, d, i, 3) if i >= 0 else False
            cols["v3_simple_oversold"][idx] = simple_oversold(k, i) if i >= 0 else False
            cols["v4_entry_week_itself"][idx] = crossed_up_oversold(k, d, entry_week_i) if entry_week_i >= 0 else False
            cols["v5_raw_k"][idx] = float(k.iloc[i]) if 0 <= i < len(k) and not pd.isna(k.iloc[i]) else None
        print(f"[{n}/{len(tickers)}] {tk}: done", file=sys.stderr)

    for c, mapping in cols.items():
        trades[c] = trades.index.map(mapping.get)

    print("\n=== VARIANTS 1-4: binary filter comparison ===")
    for c in ["v1_strict_cross_1wk", "v2_loose_cross_3wk", "v3_simple_oversold", "v4_entry_week_itself"]:
        matched = trades[trades[c].notna()]
        hit = matched[matched[c] == True]
        miss = matched[matched[c] == False]
        s_hit, s_miss = bucket_stats(hit[ret_col].values), bucket_stats(miss[ret_col].values)
        print(f"\n[{c}]  matched {len(matched)}/{len(trades)}")
        print(f"  WITH   : {s_hit['trades']:4d} trades, win {s_hit['win_rate']:.1f}%, "
              f"PF {s_hit['pf']:.2f}, exp {s_hit['expectancy']:+.2f}%")
        print(f"  WITHOUT: {s_miss['trades']:4d} trades, win {s_miss['win_rate']:.1f}%, "
              f"PF {s_miss['pf']:.2f}, exp {s_miss['expectancy']:+.2f}%")

    print("\n=== VARIANT 5: continuous %K at reference week, by quintile ===")
    valid = trades[trades["v5_raw_k"].notna()].copy()
    if len(valid) >= 5:
        valid["k_quintile"] = pd.qcut(valid["v5_raw_k"], 5, duplicates="drop")
        g = valid.groupby("k_quintile", observed=True)[ret_col].agg(["count", "mean"])
        print(g.rename(columns={"count": "trades", "mean": "avg_ret_pct"}).to_string())
    else:
        print("Not enough valid %K values for a quintile breakdown.")

    out_path = trades_path.rsplit(".", 1)[0] + "_with_stochrsi_variants.csv"
    trades.to_csv(out_path, index=False)
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
