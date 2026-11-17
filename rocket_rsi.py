#!/usr/bin/env python3
"""
John Ehlers' RocketRSI, from "RocketRSI -- A Solid Propellant For Your
Rocket Science Trading" (Stocks & Commodities, May 2018). Forty found a
TradingView indicator named "RSI KILLER John Ehlers" whose source is
protected (can't be inspected), but its one-line description ("RSI
smoothed and enhanced with minimum lag penalty") matches Ehlers' own
published RSI-turning-point family, and RocketRSI is the one he
explicitly designed to spike at cyclic tops/bottoms via a Fisher
Transform -- the exact "find the tops and bottoms" property Forty is
after. This is the REAL, documented formula (verified against the
TradersTips write-up of the original TradeStation/EasyLanguage code),
not a guess at the locked script's internals.

Steps (see TradersTips May 2018 for the original EasyLanguage):
  1. Momentum: Mom[i] = Close[i] - Close[i - (rsi_length - 1)]
  2. Two-pole Super Smoother applied to Mom (SmoothLength = smooth_length):
       a1 = exp(-1.414*pi / L)
       b1 = 2*a1*cos(1.414*pi / L)     (cosine argument in radians --
                                         Ehlers' EasyLanguage Cosine() is
                                         degrees-based, 1.414*180/L deg,
                                         which is the same angle)
       c2 = b1, c3 = -a1**2, c1 = 1 - c2 - c3
       Filt[i] = c1*(Mom[i] + Mom[i-1])/2 + c2*Filt[i-1] + c3*Filt[i-2]
  3. RSI-style ratio over the last rsi_length bars of Filt's bar-to-bar
     changes: CU = sum of positive changes, CD = sum of |negative changes|.
       MyRSI = (CU - CD) / (CU + CD)
  4. Clamp MyRSI to +/-0.999 (keeps step 5 finite).
  5. Fisher Transform: RocketRSI = 0.5 * ln((1 + MyRSI) / (1 - MyRSI))

Default rsi_length=10, smooth_length=10 (TradeStation defaults in the
article; MetaStock/eSignal used 8/10).
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def compute_rocket_rsi(close: pd.Series, rsi_length: int = 10, smooth_length: int = 10) -> pd.Series:
    c = close.values.astype(float)
    n = len(c)

    mom = np.full(n, np.nan)
    for i in range(rsi_length - 1, n):
        mom[i] = c[i] - c[i - (rsi_length - 1)]

    L = smooth_length
    a1 = np.exp(-1.414 * np.pi / L)
    b1 = 2 * a1 * np.cos(1.414 * np.pi / L)
    c2 = b1
    c3 = -a1 ** 2
    c1 = 1 - c2 - c3

    filt = np.full(n, np.nan)
    for i in range(n):
        if np.isnan(mom[i]):
            continue
        prev_mom = mom[i - 1] if i >= 1 and not np.isnan(mom[i - 1]) else mom[i]
        prev_filt1 = filt[i - 1] if i >= 1 and not np.isnan(filt[i - 1]) else 0.0
        prev_filt2 = filt[i - 2] if i >= 2 and not np.isnan(filt[i - 2]) else 0.0
        filt[i] = c1 * (mom[i] + prev_mom) / 2 + c2 * prev_filt1 + c3 * prev_filt2

    rocket = np.full(n, np.nan)
    for i in range(n):
        if i < rsi_length or np.isnan(filt[i - rsi_length:i + 1]).any():
            continue
        window = filt[i - rsi_length + 1:i + 1]
        diffs = np.diff(window)
        cu = diffs[diffs > 0].sum()
        cd = -diffs[diffs < 0].sum()
        denom = cu + cd
        if denom == 0:
            my_rsi = 0.0
        else:
            my_rsi = (cu - cd) / denom
        my_rsi = max(-0.999, min(0.999, my_rsi))
        rocket[i] = 0.5 * np.log((1 + my_rsi) / (1 - my_rsi))

    return pd.Series(rocket, index=close.index)
