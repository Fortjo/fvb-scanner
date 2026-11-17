#!/usr/bin/env python3
"""
Diagnostic: does a weekly StochRSI "oversold, turning up" reading at
entry correlate with better outcomes on an EXISTING trades log?
===========================================================================
Standalone — does NOT touch backtest_33fvb.py or milestone_c_vol_sizing.py's
live signal logic. Takes a trades CSV already produced by one of those
(e.g. milestone_c_sp500_baseline_trades.csv), fetches weekly price history
independently for every ticker involved, computes StochRSI, and joins a
flag onto each trade: was price oversold-and-turning-up as of the LAST
FULLY COMPLETED weekly bar strictly before the entry week?

This directly tests the idea from Peter's indicator (the "Oversold -
turning up" StochRSI readout he's been adding) and from the SMC/ICT
research: StochRSI used as a CONFIRMATION/timing filter on top of an
existing signal, not a new signal generator. It can only ever narrow the
existing trade list (fewer, hopefully better trades) -- it can't create
new trades that the core 33FVB signal didn't already fire.

No-lookahead convention: `entry_date` in the trades CSV is the real daily
fill date (from the corrected event-driven engine). We find the weekly
bar whose week CONTAINS entry_date, then step back ONE MORE weekly bar --
the last week that had already fully closed before the entry week even
started. That is the StochRSI state a trader actually had in hand before
the signal fired, never any information from the entry week itself.

RSI here uses a standard Wilder-style EWM approximation (alpha=1/14), not
an exact re-implementation of any particular charting platform's RSI --
close enough for this exploratory filter test, not claimed to match
Peter's indicator bar-for-bar.

Needs network (yfinance) -- this sandbox has none (confirmed: 403 from
the proxy on a yfinance test fetch). Run this on a machine with real
network access, same as milestone_c_vol_sizing.py's own real-data runs.

Usage:
    pip install yfinance pandas numpy
    python3 test_stochrsi_filter.py milestone_c_sp500_baseline_trades.csv
"""
import sys
import numpy as np
import pandas as pd
import backtest_33fvb as bt

RSI_LEN = 14
STOCH_LEN = 14
K_SMOOTH = 3
D_SMOOTH = 3
OVERSOLD = 20


def compute_stoch_rsi(close: pd.Series):
    """Standard StochRSI: Stochastic oscillator applied to RSI instead of
    price. Returns (k, d) as pd.Series aligned to `close`'s index."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / RSI_LEN, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / RSI_LEN, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    rsi = rsi.where(avg_loss != 0, 100.0)  # pure-gain stretch -> RSI=100, standard convention
    rsi_min = rsi.rolling(STOCH_LEN).min()
    rsi_max = rsi.rolling(STOCH_LEN).max()
    stoch = 100 * (rsi - rsi_min) / (rsi_max - rsi_min).replace(0, np.nan)
    k = stoch.rolling(K_SMOOTH).mean()
    d = k.rolling(D_SMOOTH).mean()
    return k, d


def oversold_turning_up(k: pd.Series, d: pd.Series, i: int) -> bool:
    """True if, as of fully-completed bar i, %K just crossed above %D
    while both were recently under the oversold line. Requires i-1 to
    exist too (the cross needs two bars)."""
    if i < 1 or i >= len(k):
        return False
    vals = [k.iloc[i], k.iloc[i - 1], d.iloc[i], d.iloc[i - 1]]
    if any(pd.isna(v) for v in vals):
        return False
    crossed_up = k.iloc[i - 1] <= d.iloc[i - 1] and k.iloc[i] > d.iloc[i]
    was_oversold = min(vals) < OVERSOLD
    return bool(crossed_up and was_oversold)


def bucket_stats(rets_pct: np.ndarray) -> dict:
    if len(rets_pct) == 0:
        return dict(trades=0, win_rate=np.nan, pf=np.nan, expectancy=np.nan)
    r = rets_pct / 100.0
    wins, losses = r[r > 0], r[r <= 0]
    return dict(
        trades=len(r),
        win_rate=100 * len(wins) / len(r),
        pf=(wins.sum() / -losses.sum()) if losses.sum() < 0 else np.inf,
        expectancy=100 * r.mean(),
    )


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 test_stochrsi_filter.py <trades_csv>", file=sys.stderr)
        sys.exit(1)
    trades_path = sys.argv[1]
    trades = pd.read_csv(trades_path, parse_dates=["entry_date", "exit_date"])
    ret_col = "net_ret_pct" if "net_ret_pct" in trades.columns else "ret_pct"
    if ret_col not in trades.columns:
        print(f"Couldn't find a return column (looked for net_ret_pct / ret_pct) in {trades_path}. "
              f"Columns present: {list(trades.columns)}", file=sys.stderr)
        sys.exit(1)

    tickers = sorted(trades["ticker"].unique())
    print(f"{len(trades)} trades across {len(tickers)} tickers. Fetching weekly price history...",
          file=sys.stderr)

    flag_by_idx = {}
    for n, tk in enumerate(tickers, 1):
        sub = trades[trades["ticker"] == tk]
        try:
            weekly = bt.fetch(tk)
        except Exception as e:
            print(f"{tk}: fetch error {e}", file=sys.stderr)
            weekly = None
        if weekly is None or len(weekly) < RSI_LEN + STOCH_LEN + 10:
            print(f"{tk}: insufficient data, leaving {len(sub)} trade(s) unflagged", file=sys.stderr)
            for idx in sub.index:
                flag_by_idx[idx] = None
            continue
        k, d = compute_stoch_rsi(weekly["Close"])
        for idx, row in sub.iterrows():
            entry_date = row["entry_date"]
            # first weekly bar whose week-start is > entry_date
            pos = weekly.index.searchsorted(entry_date, side="right")
            entry_week_i = pos - 1          # the week CONTAINING entry_date
            i = entry_week_i - 1            # last FULLY COMPLETED week before that
            flag_by_idx[idx] = oversold_turning_up(k, d, i) if i >= 0 else False
        print(f"[{n}/{len(tickers)}] {tk}: done", file=sys.stderr)

    trades["stochrsi_oversold_turning_up"] = trades.index.map(flag_by_idx.get)

    matched = trades[trades["stochrsi_oversold_turning_up"].notna()]
    hit = matched[matched["stochrsi_oversold_turning_up"] == True]
    miss = matched[matched["stochrsi_oversold_turning_up"] == False]
    unmatched_n = len(trades) - len(matched)

    print("\n=== STOCHRSI 'OVERSOLD, TURNING UP' FILTER vs EXISTING TRADES ===")
    print(f"Matched {len(matched)}/{len(trades)} trades with usable price history "
          f"({unmatched_n} skipped for insufficient/failed data).\n")
    for label, sub in [("WITH oversold-turning-up at entry", hit),
                        ("WITHOUT (everything else)", miss)]:
        s = bucket_stats(sub[ret_col].values)
        if s["trades"] == 0:
            print(f"{label}: 0 trades")
            continue
        print(f"{label}: {s['trades']} trades, win rate {s['win_rate']:.1f}%, "
              f"PF {s['pf']:.2f}, expectancy {s['expectancy']:+.2f}%/trade")

    out_path = trades_path.rsplit(".", 1)[0] + "_with_stochrsi_flag.csv"
    trades.to_csv(out_path, index=False)
    print(f"\nSaved: {out_path}")
    print("\nRead this as a diagnostic, not a verdict: this filter only ever REMOVES trades from the "
          "existing list (it can't create new ones), so compare the two buckets' expectancy/PF, not "
          "just win rate, before deciding whether it's worth adding as a real entry gate.")


if __name__ == "__main__":
    main()
