#!/usr/bin/env python3
"""
VIX regime at entry vs FVB trade outcomes (chat, 2026-11-25).

Context: IncomeSharks' "Shark SPY Day Trader" claims its win rate jumps from
~72% to ~85% when VIX > 17. That's an INTRADAY claim. This asks the
bigger-timeframe version of the same question on OUR system, where there is
36 years of free history to test it (VIX starts Jan 1990): does the VIX level
at the time of an FVB entry tell us anything about how that trade turns out?

Two layers, same standard as every other filter we've tested:
  1) Trade level: win rate / mean return bucketed by VIX at entry
     (prior week's VIX close -- known at entry, no look-ahead).
  2) Portfolio level (portfolio_sim, the repaired engine): gate NEW entries
     by a VIX rule and compare CAGR / max drawdown / Sharpe to no filter,
     against a CALENDAR-CIRCULAR-SHIFT placebo -- the real week-by-week
     ON/OFF path rotated to random offsets, which keeps VIX's months-long
     autocorrelation intact (the flaw found in the first market-filter
     test). p-values use (+1)/(N+1).
     Two rules, both fixed in advance (not tuned):
       R1 "skip in panic":       skip new entries when VIX >= 30
       R2 "only when fearful":   take new entries only when VIX >= 20
     Two rules = two tests; read any p-value near 0.05 with that in mind.

Only trades entered from Feb 1990 on are used in every version (A, B and the
placebo), so all comparisons cover the same period. Read-only research.
"""
from __future__ import annotations
import sys
import numpy as np
import pandas as pd
import yfinance as yf
import backtest_33fvb as bt
from portfolio_sim import build_portfolio, metrics

SLOTS = 15
CASH_R_WEEKLY = 0.0008
N_PLACEBO = 300
SEED = 42
MIN_SHIFT_WEEKS = 52


def vix_weekly():
    d = yf.download("^VIX", start="1990-01-01", interval="1wk", auto_adjust=False, progress=False)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    return d["Close"].dropna().shift(1).dropna()   # prior week's close = known at entry


def collect_trades():
    out = []
    for tk in bt.UNIVERSE:
        try:
            df = bt.fetch(tk)
            if df is None or len(df) < bt.FVB_LEN * 5:
                continue
            trs = bt.run(bt.prep(df), band="High", exit_mode="NBarHigh")
        except Exception as e:
            print(f"{tk}: error {e}", file=sys.stderr)
            continue
        for t in trs:
            out.append((tk, t["entry_date"], t["exit_date"], float(t["ret"])))
    return out


def main():
    vix = vix_weekly()
    raw = collect_trades()
    rows = []
    for tk, ed, xd, ret in raw:
        if ed < vix.index[0]:
            continue
        v = vix.asof(ed)
        if pd.isna(v):
            continue
        rows.append((tk, ed, xd, ret, float(v)))
    print(f"{len(raw)} raw FVB trades, {len(rows)} entered since VIX history starts (Feb 1990)\n")

    # ---------- 1) trade level ----------
    df = pd.DataFrame(rows, columns=["tk", "ed", "xd", "ret", "vix"])
    bins = [0, 15, 20, 30, 200]
    labels = ["VIX under 15", "VIX 15-20", "VIX 20-30", "VIX 30+"]
    df["bucket"] = pd.cut(df.vix, bins=bins, labels=labels, right=False)
    print("TRADE LEVEL -- outcome by VIX at entry")
    for b in labels:
        s = df[df.bucket == b]
        if len(s):
            print(f"  {b:13s}  trades {len(s):5d}   win {100*(s.ret>0).mean():5.1f}%   "
                  f"mean ret {100*s.ret.mean():+6.1f}%   median {100*s.ret.median():+6.1f}%")
    print(f"  {'all':13s}  trades {len(df):5d}   win {100*(df.ret>0).mean():5.1f}%   "
          f"mean ret {100*df.ret.mean():+6.1f}%")

    # ---------- 2) portfolio level ----------
    trades = [(tk, ed, xd, ret, max((xd - ed).days / 7.0, 1 / 7)) for tk, ed, xd, ret, _ in rows]
    eqA, takenA, _ = build_portfolio(trades, SLOTS, lambda tk, t: False, CASH_R_WEEKLY)
    mA = metrics(eqA)
    print(f"\nPORTFOLIO LEVEL ({SLOTS} slots, repaired engine)")
    print(f"  A) no filter:  CAGR {100*mA['cagr']:+.1f}%  max DD {100*mA['dd']:+.1f}%  "
          f"Sharpe {mA['sharpe']:.2f}  entries {takenA}")

    rules = {
        "R1 skip new entries when VIX >= 30": (vix >= 30),
        "R2 enter only when VIX >= 20": (vix < 20),
    }
    rng = np.random.default_rng(SEED)
    n_weeks = len(vix)
    for name, skip_series in rules.items():
        skip_series = skip_series.astype(bool)

        def mk_skip(series):
            return lambda tk, t: bool(series.asof(t)) if t >= series.index[0] else False

        eqB, takenB, skippedB = build_portfolio(trades, SLOTS, mk_skip(skip_series), CASH_R_WEEKLY)
        mB = metrics(eqB)
        d_dd = mB["dd"] - mA["dd"]
        d_sh = mB["sharpe"] - mA["sharpe"]
        d_cagr = mB["cagr"] - mA["cagr"]

        p_dd, p_sh = [], []
        for _ in range(N_PLACEBO):
            k = int(rng.integers(MIN_SHIFT_WEEKS, n_weeks - MIN_SHIFT_WEEKS))
            shifted = pd.Series(np.roll(skip_series.values, k), index=skip_series.index)
            eqC, _, _ = build_portfolio(trades, SLOTS, mk_skip(shifted), CASH_R_WEEKLY)
            mC = metrics(eqC)
            p_dd.append(mC["dd"] - mA["dd"])
            p_sh.append(mC["sharpe"] - mA["sharpe"])
        p_dd, p_sh = np.array(p_dd), np.array(p_sh)
        pv_dd = (np.sum(p_dd >= d_dd) + 1) / (N_PLACEBO + 1)
        pv_sh = (np.sum(p_sh >= d_sh) + 1) / (N_PLACEBO + 1)

        print(f"\n  B) {name}  (rule ON {100*skip_series.mean():.0f}% of weeks)")
        print(f"     CAGR {100*mB['cagr']:+.1f}% ({100*d_cagr:+.2f}pts)  max DD {100*mB['dd']:+.1f}% "
              f"({100*d_dd:+.1f}pts)  Sharpe {mB['sharpe']:.2f} ({d_sh:+.2f})  "
              f"taken {takenB} skipped {skippedB}")
        print(f"     placebo (calendar circular shift, {N_PLACEBO} runs): "
              f"DD improvement p={pv_dd:.3f}   Sharpe improvement p={pv_sh:.3f}")


if __name__ == "__main__":
    main()
