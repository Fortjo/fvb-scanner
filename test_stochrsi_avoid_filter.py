#!/usr/bin/env python3
"""
Deeper test of the one StochRSI result that actually looked real: avoid a
signal when weekly StochRSI %K is oversold at entry, rather than requiring
it turn up (the original hypothesis -- rejected, see test_stochrsi_filter.py
and test_stochrsi_variants.py: 3 of 4 "require oversold-turning-up" variants
hurt, and v3 "simple_oversold" showed a large, clean gap -- 347 trades,
58.8% win/PF 1.82/+7.63% exp when oversold, vs 692 trades, 70.1% win/
PF 2.52/+9.67% exp otherwise).

This checks ROBUSTNESS on three axes before that becomes something worth
wiring in for real, instead of taking one number at face value:

  1. THRESHOLD sensitivity -- is 20 special, or does any nearby cutoff
     (10/15/20/25/30) show roughly the same split? A result that only
     shows up at one arbitrary threshold is much weaker evidence than one
     that holds across a range.
  2. LAG sensitivity -- does it hold using BOTH the conservative
     1-week-lagged reference bar AND the entry week's own bar? The
     require-oversold-turning-up test flipped sign between these two
     (v1 vs v4) -- a real effect should at least point the same direction
     either way, even if the exact magnitude differs.
  3. TIME CONCENTRATION -- is "oversold = worse" spread across many years,
     or mostly one or two outlier stretches (a market-wide drawdown, where
     StochRSI would read oversold almost everywhere at once)? That would
     make this mostly a restatement of "avoid trading during crashes" --
     a different, coarser idea than a per-ticker StochRSI read, and one
     partially covered already by daily_structure/market_regime.

Reuses one fetch pass per ticker, same as test_stochrsi_variants.py. The
network-dependent fetch loop is separated from analyze() specifically so
analyze() can be (and is, below) unit-tested offline with synthetic data.

Needs network -- run on a machine with it.

Usage:
    python3 test_stochrsi_avoid_filter.py milestone_c_sp500_baseline_trades.csv
"""
import sys
import numpy as np
import pandas as pd
import backtest_33fvb as bt
from test_stochrsi_filter import compute_stoch_rsi, bucket_stats
from test_stochrsi_variants import entry_week_index

THRESHOLDS = [10, 15, 20, 25, 30]


def fetch_k_references(trades: pd.DataFrame) -> pd.DataFrame:
    """Network-dependent step: for every trade, fetches its ticker's
    weekly history (once per ticker) and records raw %K at both the
    1-week-lagged reference bar and the entry week's own bar. Returns a
    copy of `trades` with k_lagged / k_entry_week columns added."""
    trades = trades.copy()
    tickers = sorted(trades["ticker"].unique())
    print(f"{len(trades)} trades across {len(tickers)} tickers. Fetching weekly price history once...",
          file=sys.stderr)

    k_lagged, k_entry_week = {}, {}
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
                k_lagged[idx] = None
                k_entry_week[idx] = None
            continue
        k, d = compute_stoch_rsi(weekly["Close"])
        for idx, row in sub.iterrows():
            entry_week_i, i = entry_week_index(weekly.index, row["entry_date"])
            k_lagged[idx] = float(k.iloc[i]) if 0 <= i < len(k) and not pd.isna(k.iloc[i]) else None
            k_entry_week[idx] = (float(k.iloc[entry_week_i])
                                  if 0 <= entry_week_i < len(k) and not pd.isna(k.iloc[entry_week_i]) else None)
        print(f"[{n}/{len(tickers)}] {tk}: done", file=sys.stderr)

    trades["k_lagged"] = trades.index.map(k_lagged.get)
    trades["k_entry_week"] = trades.index.map(k_entry_week.get)
    return trades


def threshold_lag_table(trades: pd.DataFrame, ret_col: str) -> pd.DataFrame:
    """Axes 1 & 2: for each (reference bar, threshold) combo, splits
    trades into avoid (k < threshold) / keep (k >= threshold) and reports
    both buckets' stats. Returns a tidy DataFrame (also printed by main())."""
    rows = []
    for ref_col, ref_label in [("k_lagged", "1wk-lag"), ("k_entry_week", "entry-wk")]:
        if ref_col not in trades.columns:
            continue
        valid = trades[trades[ref_col].notna()]
        for th in THRESHOLDS:
            avoid = valid[valid[ref_col] < th]
            keep = valid[valid[ref_col] >= th]
            sa, sk = bucket_stats(avoid[ret_col].values), bucket_stats(keep[ret_col].values)
            rows.append({"reference": ref_label, "threshold": th,
                         "avoid_n": sa["trades"], "avoid_win_pct": sa["win_rate"],
                         "avoid_pf": sa["pf"], "avoid_exp_pct": sa["expectancy"],
                         "keep_n": sk["trades"], "keep_win_pct": sk["win_rate"],
                         "keep_pf": sk["pf"], "keep_exp_pct": sk["expectancy"]})
    return pd.DataFrame(rows)


def yearly_concentration_table(trades: pd.DataFrame, ret_col: str, threshold: int = 20,
                                ref_col: str = "k_lagged") -> pd.DataFrame:
    """Axis 3: per-year split of the default (threshold=20, 1wk-lag)
    oversold/not-oversold buckets, so a result driven by one or two
    outlier years is visible rather than hidden inside one aggregate
    number."""
    valid = trades[trades[ref_col].notna()].copy()
    valid["entry_year"] = pd.to_datetime(valid["entry_date"]).dt.year
    valid["oversold_avoid"] = valid[ref_col] < threshold
    rows = []
    for yr, grp in valid.groupby("entry_year"):
        ov = grp[grp["oversold_avoid"]]
        nv = grp[~grp["oversold_avoid"]]
        s_ov, s_nv = bucket_stats(ov[ret_col].values), bucket_stats(nv[ret_col].values)
        rows.append({"year": yr, "total": len(grp), "oversold_n": len(ov),
                     "oversold_share_pct": round(100 * len(ov) / len(grp), 1) if len(grp) else np.nan,
                     "oversold_exp_pct": round(s_ov["expectancy"], 2) if s_ov["trades"] else np.nan,
                     "kept_exp_pct": round(s_nv["expectancy"], 2) if s_nv["trades"] else np.nan})
    return pd.DataFrame(rows).sort_values("year").reset_index(drop=True)


def analyze(trades: pd.DataFrame, ret_col: str, out_path: str = None):
    """Pure analysis, no network -- takes a trades df that already has
    k_lagged/k_entry_week columns (from fetch_k_references, or supplied
    directly by a test), prints both tables, saves the enriched CSV if
    out_path is given, and returns (threshold_table, yearly_table) so a
    test can assert on them directly instead of parsing printed text."""
    th_table = threshold_lag_table(trades, ret_col)
    print("\n=== THRESHOLD x LAG SENSITIVITY ===")
    print(th_table.to_string(index=False))

    yr_table = yearly_concentration_table(trades, ret_col)
    print("\n=== TIME CONCENTRATION (threshold=20, 1wk-lag reference) ===")
    print(yr_table.to_string(index=False))

    if out_path:
        trades.to_csv(out_path, index=False)
        print(f"\nSaved: {out_path}")

    print("\nRead the threshold table for STABILITY (does 'keep' beat 'avoid' across most "
          "thresholds and both reference points, not just one), and the yearly table for "
          "CONCENTRATION (is the gap spread across many years, or mostly one or two outliers).")
    return th_table, yr_table


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 test_stochrsi_avoid_filter.py <trades_csv>", file=sys.stderr)
        sys.exit(1)
    trades_path = sys.argv[1]
    trades = pd.read_csv(trades_path, parse_dates=["entry_date", "exit_date"])
    ret_col = "net_ret_pct" if "net_ret_pct" in trades.columns else "ret_pct"
    if ret_col not in trades.columns:
        print(f"Couldn't find a return column in {trades_path}. Columns: {list(trades.columns)}",
              file=sys.stderr)
        sys.exit(1)

    trades = fetch_k_references(trades)
    out_path = trades_path.rsplit(".", 1)[0] + "_with_stochrsi_avoid_analysis.csv"
    analyze(trades, ret_col, out_path=out_path)


if __name__ == "__main__":
    main()
