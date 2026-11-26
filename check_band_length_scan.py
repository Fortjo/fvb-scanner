#!/usr/bin/env python3
"""
Which band length reproduces a value Peter shows on his chart?
2026-11-25 BE weekly chart, "THT Smart Money Bands 161.25" (monthly steps
overlaid on the weekly chart). Our validated 33-month FVB gives ~92/112/77
for BE -- far off -- so scan lengths and both the current and prior (promoted)
month for basis / upper / lower, and report the closest matches.
Usage: python check_band_length_scan.py "BE 161.25"
"""
import sys
import pandas as pd
import yfinance as yf
from backtest_33fvb import _compute_real_fvb

args = (sys.argv[1] if len(sys.argv) > 1 else "BE 161.25").split()
tk, target = args[0], float(args[1])
d = yf.download(tk, start="1990-01-01", interval="1d", auto_adjust=True, progress=False)
if isinstance(d.columns, pd.MultiIndex):
    d.columns = d.columns.get_level_values(0)
res = []
for tf, rule in (("monthly", "ME"), ("weekly", "W-FRI")):
    m = d.resample(rule).agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    for L in range(3, 61):
        if len(m) < L + 2:
            continue
        g, u, l = _compute_real_fvb(m, L)
        b = ((m.Open + m.High + m.Low + m.Close) / 4).rolling(L).mean()
        for lag, name in ((-1, "current"), (-2, "prior")):
            for series, lab in ((b, "basis"), (u, "upper"), (l, "lower")):
                v = series.iloc[lag]
                if pd.notna(v):
                    res.append((abs(v - target), tf, L, name, lab, round(float(v), 2), bool(g.iloc[lag])))
res.sort()
print(f"{tk}: target {target}. Closest 15:")
for r in res[:15]:
    print(f"  {r[1]:8s} len {r[2]:3d}  {r[3]:8s} {r[4]:6s} = {r[5]:8.2f}  (off {r[0]:.2f}, green={r[6]})")
