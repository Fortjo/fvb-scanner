#!/usr/bin/env python3
"""
Does Peter's shaded Smart Money Band zone = our median-deviation band method
(same as the FVB) built around his 20-month EMA(OHLC4) centre line?
Rough zone edges read off his charts by pixel (+/- ~$3):
  NVDA 2026-11-25: centre 180.00, top ~198, bottom ~152
  BE   2026-11-25: centre 161.25, top ~197, bottom ~137
Prints our computed centre/upper/lower for comparison. Print-only.
"""
import pandas as pd
import yfinance as yf
from test_ema20_band_system import bands_around

OBS = [("NVDA", "2026-11-25", 180.00, 198, 152), ("BE", "2026-11-25", 161.25, 197, 137),
       ("ARM", "2026-11-25", 198.45, None, None), ("AVGO", "2026-11-25", 321.49, None, None),
       ("AAOI", "2026-11-25", 76.01, None, None)]

for tk, cutoff, c, top, bot in OBS:
    d = yf.download(tk, start="1990-01-01", interval="1d", auto_adjust=True, progress=False)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    d = d[d.index <= cutoff]
    m = d.resample("ME").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    ema = ((m.Open + m.High + m.Low + m.Close) / 4).ewm(span=20, adjust=False).mean()
    green, up, lo = bands_around(m, ema)
    seen = f"his top ~{top}, bottom ~{bot}" if top else "zone not measured"
    print(f"{tk:5s} centre {ema.iloc[-1]:8.2f} (his {c})  ours: upper {up.iloc[-1]:8.2f}  lower {lo.iloc[-1]:8.2f}"
          f"  green={bool(green.iloc[-1])} | {seen}")
