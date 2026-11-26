#!/usr/bin/env python3
"""
Which band formula reproduces the values Peter shows on his own charts?
His newer "THT Smart Money Bands" indicator (monthly steps, shown on weekly and
monthly charts) -- readings from his 2026-11-25 posts:
    BE 161.25, ARM 198.45, AVGO 321.49
Our validated 33-month FVB gives ~92/112/77 for BE -- far off -- so this scans
one shared configuration (timeframe, length, line, current/prior month) that
fits ALL readings at once, ranked by worst-case % error. One reading alone
can't pin a formula down; several tickers on the same date can.
Candidates: FVB basis/upper/lower (our validated band math), SMA/EMA of close,
EMA of OHLC4.
Usage: python check_band_length_scan.py "BE:161.25,ARM:198.45,AVGO:321.49"
"""
import sys
import pandas as pd
import yfinance as yf
from backtest_33fvb import _compute_real_fvb

spec = sys.argv[1] if len(sys.argv) > 1 else "BE:161.25,ARM:198.45,AVGO:321.49"
targets = {k: float(v) for k, v in (p.split(":") for p in spec.split(","))}

data = {}
for tk in targets:
    d = yf.download(tk, start="1990-01-01", interval="1d", auto_adjust=True, progress=False)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    d = d[d.index <= "2026-11-25"]
    data[tk] = d

scores = {}
for tf, rule in (("monthly", "ME"), ("weekly", "W-FRI")):
    for tk, d in data.items():
        m = d.resample(rule).agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
        o4 = (m.Open + m.High + m.Low + m.Close) / 4
        for L in range(3, 61):
            if len(m) < L + 3:
                continue
            g, u, l = _compute_real_fvb(m, L)
            cands = {
                "FVB basis": o4.rolling(L).mean(), "FVB upper": u, "FVB lower": l,
                "SMA close": m.Close.rolling(L).mean(), "EMA close": m.Close.ewm(span=L, adjust=False).mean(),
                "EMA ohlc4": o4.ewm(span=L, adjust=False).mean(),
            }
            for lag, lagname in ((-1, "current"), (-2, "prior")):
                for name, s in cands.items():
                    v = s.iloc[lag]
                    if pd.isna(v):
                        continue
                    key = (tf, L, name, lagname)
                    scores.setdefault(key, {})[tk] = float(v)

rows = []
for key, vals in scores.items():
    if len(vals) < len(targets):
        continue
    errs = {tk: 100 * (vals[tk] - targets[tk]) / targets[tk] for tk in targets}
    rows.append((max(abs(e) for e in errs.values()), key, vals, errs))
rows.sort(key=lambda r: r[0])
print("targets:", targets)
print("Best 12 shared configurations (worst-case % error across all tickers):")
for worst, (tf, L, name, lag), vals, errs in rows[:12]:
    detail = "  ".join(f"{tk} {vals[tk]:.2f} ({errs[tk]:+.1f}%)" for tk in targets)
    print(f"  {worst:5.1f}%  {tf:7s} len {L:2d}  {name:9s} {lag:7s} | {detail}")
best33 = [r for r in rows if r[1][1] == 33 and r[1][0] == "monthly"][:3]
print("\nFor reference, our 33-month configs:")
for worst, (tf, L, name, lag), vals, errs in best33:
    print(f"  {worst:5.1f}%  {name} {lag} | " + "  ".join(f"{tk} {vals[tk]:.2f}" for tk in targets))
