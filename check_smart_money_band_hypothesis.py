#!/usr/bin/env python3
"""
Out-of-sample test of the hypothesis found by check_band_length_scan.py:
Peter's "THT Smart Money Bands" centre line = 20-period EMA of OHLC4,
computed on the chart's band timeframe (monthly steps; a second smooth copy on
SMH looks weekly). Fitted on BE/ARM/AVGO (2026-11-25). Tested here on readings
it was NOT fitted on:
  AAOI 76.01  monthly  (chart dated 2026-11-25)
  SMH 431.23  monthly  (posted 2026-11-22 -> data through 2026-11-20)
  SMH 537.72  weekly?  (same chart, smooth line)
Print-only.
"""
import pandas as pd
import yfinance as yf

TESTS = [("AAOI", "2026-11-25", "ME", 76.01), ("SMH", "2026-11-20", "ME", 431.23),
         ("SMH", "2026-11-20", "W-FRI", 537.72),
         ("BE", "2026-11-25", "ME", 161.25), ("ARM", "2026-11-25", "ME", 198.45),
         ("AVGO", "2026-11-25", "ME", 321.49)]

for tk, cutoff, rule, target in TESTS:
    d = yf.download(tk, start="1990-01-01", interval="1d", auto_adjust=True, progress=False)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    d = d[d.index <= cutoff]
    m = d.resample(rule).agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    o4 = (m.Open + m.High + m.Low + m.Close) / 4
    out = []
    for L in (20,):
        v = o4.ewm(span=L, adjust=False).mean().iloc[-1]
        out.append(f"EMA{L} ohlc4 = {v:.2f} ({100*(v-target)/target:+.2f}%)")
    # unadjusted too (TradingView ADJ toggle)
    d2 = yf.download(tk, start="1990-01-01", interval="1d", auto_adjust=False, progress=False)
    if isinstance(d2.columns, pd.MultiIndex):
        d2.columns = d2.columns.get_level_values(0)
    d2 = d2[d2.index <= cutoff]
    m2 = d2.resample(rule).agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    v2 = ((m2.Open + m2.High + m2.Low + m2.Close) / 4).ewm(span=20, adjust=False).mean().iloc[-1]
    tf = "monthly" if rule == "ME" else "weekly"
    print(f"{tk:5s} {tf:7s} target {target:8.2f} | {' '.join(out)} | unadjusted {v2:.2f} ({100*(v2-target)/target:+.2f}%)")
