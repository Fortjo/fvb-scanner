#!/usr/bin/env python3
"""
Print our current monthly FVB band values for a ticker, to compare against a
number Peter shows on his own chart (e.g. 2026-11-25 BE weekly chart:
"THT Smart Money Bands 161.25"). Calibration check only -- print-only.
Usage: python check_band_value.py BE
"""
import sys
import pandas as pd
import yfinance as yf
from backtest_33fvb import _compute_real_fvb, FVB_LEN


def main(tk="BE"):
    d = yf.download(tk, start="1990-01-01", interval="1d", auto_adjust=True, progress=False)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    m = d.resample("ME").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    green, upper, lower = _compute_real_fvb(m, FVB_LEN)
    ohlc4 = (m.Open + m.High + m.Low + m.Close) / 4
    basis = ohlc4.rolling(FVB_LEN).mean()
    print(f"{tk}: last close {m.Close.iloc[-1]:.2f}")
    print("month        basis    upper    lower   green   (also prior month = what Pine shows after 1-month promotion delay)")
    for i in range(-4, 0):
        print(f"{m.index[i].date()}  {basis.iloc[i]:8.2f} {upper.iloc[i]:8.2f} {lower.iloc[i]:8.2f}   {bool(green.iloc[i])}")
    # same numbers with ADJUSTED=False too, since TradingView's ADJ toggle matters
    d2 = yf.download(tk, start="1990-01-01", interval="1d", auto_adjust=False, progress=False)
    if isinstance(d2.columns, pd.MultiIndex):
        d2.columns = d2.columns.get_level_values(0)
    m2 = d2.resample("ME").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    g2, u2, l2 = _compute_real_fvb(m2, FVB_LEN)
    b2 = ((m2.Open + m2.High + m2.Low + m2.Close) / 4).rolling(FVB_LEN).mean()
    print(f"unadjusted, latest: basis {b2.iloc[-1]:.2f} upper {u2.iloc[-1]:.2f} lower {l2.iloc[-1]:.2f}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "BE")
