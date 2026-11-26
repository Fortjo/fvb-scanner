#!/usr/bin/env python3
"""
Does faster capital rotation compound better? (chat, 2026-11-26)

Forty's question: FVB trades are great but take months; Silver Bullet (SB) trades finish in
~3 weeks. Does taking profits more often and rotating capital compound faster?

Compares, on the same universe and simulator (portfolio_sim, 15 slots):
  FVB only          our live 33-month system
  SB only           Silver Bullet as backtested in test_silver_bullet.py
  FVB + SB          both signal lists in one portfolio, first come first served -- SB trades
                    use slots/cash that FVB isn't using at that moment
And per-trade "return per month held" for each, plus a cost check: Trading 212 charges a
currency-conversion fee on USD stocks for a EUR account (as far as we know ~0.15% each way),
so each round trip costs ~0.3% + spread, not the 0.1% used in earlier tests.
Read-only research.
"""
from __future__ import annotations
import sys
import numpy as np
import pandas as pd
import yfinance as yf
import backtest_33fvb as bt
from portfolio_sim import build_portfolio, metrics
import test_silver_bullet as sbm

SLOTS = 15
CASH_R_WEEKLY = 0.0008


def per_month(tr):
    r = np.array([t[3] for t in tr]); d = np.array([(t[2] - t[1]).days for t in tr])
    return 100 * r.mean(), np.median(d), 100 * r.sum() / max(d.sum(), 1) * 30


def port(tr, extra_cost=0.0):
    pt = [(tk, ed, xd, ret - extra_cost, max((xd - ed).days / 7.0, 1 / 7)) for tk, ed, xd, ret in tr]
    eq, taken, _ = build_portfolio(pt, SLOTS, lambda a, b: False, CASH_R_WEEKLY)
    return metrics(eq), taken, eq


def main():
    fvb, sb = [], []
    for tk in bt.UNIVERSE:
        try:
            w = bt.fetch(tk)
            if w is not None and len(w) >= bt.FVB_LEN * 5:
                for t in bt.run(bt.prep(w), band="High", exit_mode="NBarHigh"):
                    fvb.append((tk, t["entry_date"], t["exit_date"], float(t["ret"])))
            d = yf.download(tk, start="1995-01-01", interval="1d", auto_adjust=True, progress=False)
            if isinstance(d.columns, pd.MultiIndex):
                d.columns = d.columns.get_level_values(0)
            d = d[["Open", "High", "Low", "Close"]].dropna()
            if len(d) >= 800:
                bull = sbm.bull_filter(d).fillna(False)
                for t in sbm.run_sb(d, bull, True):
                    sb.append((tk, t["entry_date"], t["exit_date"], float(t["ret"])))
        except Exception as e:
            print(f"{tk}: {e}", file=sys.stderr)

    start = pd.Timestamp("1995-06-01")
    fvb = [t for t in fvb if t[1] >= start]
    sb = [t for t in sb if t[1] >= start]
    print(f"Same period for all: trades entered from {start.date()}\n")
    print("PER TRADE")
    for name, tr in (("FVB", fvb), ("Silver Bullet", sb)):
        m, hold, pm = per_month(tr)
        print(f"  {name:14s} trades {len(tr):5d}  avg {m:+5.1f}%  median hold {hold:4.0f} days  "
              f"return per month the money is in the trade {pm:+5.2f}%")

    print(f"\nPORTFOLIO ({SLOTS} slots)        cost 0.1% RT              cost 0.4% RT (FX fee + spread)")
    for name, tr in (("FVB only", fvb), ("SB only", sb), ("FVB + SB together", fvb + sb)):
        a, ta, eqa = port(tr, 0.0)
        b, tb, _ = port(tr, 0.003)
        print(f"  {name:20s}  CAGR {100*a['cagr']:+5.1f}% DD {100*a['dd']:+6.1f}% ({ta} entries)   "
              f"CAGR {100*b['cagr']:+5.1f}% DD {100*b['dd']:+6.1f}%")
        if name == "FVB only":
            # how much of the time is money actually invested? approx: slots used per week
            pass


if __name__ == "__main__":
    main()
