#!/usr/bin/env python3
"""
Peter's new "THT Smart Money Bands" (reverse-engineered 2026-11-26 from six
chart readings, two out-of-sample exact: centre line = 20-period EMA of OHLC4,
monthly) vs our validated 33-month FVB system. Same universe, same engine
(backtest_33fvb.run), same exits (NBarHigh TP, basis_slope stop), same costs.

Variants, fixed in advance (not tuned):
  BASE  our validated live config: 33-month SMA(OHLC4) basis, median-deviation
        bands, sticky cross state, entry when weekly low touches upper band.
  A     minimal swap: identical to BASE except the basis is the 20-month EMA
        of OHLC4 (bands + sticky state rebuilt around it the same way).
  B     Peter-literal reading: bull cycle = 20-month EMA rising; entry when the
        weekly low touches the EMA line itself; stop when the EMA turns down.
All monthly values use the last COMPLETED month (shift 1), as in BASE.

Also reruns the SPY "18 of 20 bounces" check (Peter's 2026-11-23 email) with
the 20-month EMA line -- our 33-month band only gave 11 episodes / 9 bounces.
Read-only research.
"""
from __future__ import annotations
import sys
from collections import deque
import numpy as np
import pandas as pd
import backtest_33fvb as bt
from portfolio_sim import build_portfolio, metrics

EMA_LEN = 20
SLOTS = 15
CASH_R_WEEKLY = 0.0008


def bands_around(m: pd.DataFrame, basis: pd.Series):
    """Same median-deviation band + sticky cross state as bt._compute_real_fvb,
    but around an arbitrary basis series."""
    low_spread, high_spread = m["Low"] / basis, m["High"] / basis
    straddle = (m["Low"] < basis) & (m["High"] > basis)
    n = len(m)
    dev_up = np.where(straddle, high_spread, np.nan)
    dev_dn = np.where(straddle, low_spread, np.nan)
    mu, md = np.full(n, np.nan), np.full(n, np.nan)
    uw, dw = deque(maxlen=1000), deque(maxlen=1000)
    for i in range(n):
        uw.append(dev_up[i]); dw.append(dev_dn[i])
        vu = [x for x in uw if not np.isnan(x)]; vd = [x for x in dw if not np.isnan(x)]
        if vu: mu[i] = np.median(vu)
        if vd: md[i] = np.median(vd)
    upper, lower = basis.values * mu, basis.values * md
    state, out = 0, np.zeros(n, dtype=int)
    hi, lo = m["High"].values, m["Low"].values
    for i in range(n):
        if not np.isnan(lower[i]) and hi[i] < lower[i]:
            state = -1
        elif not np.isnan(upper[i]) and lo[i] > upper[i]:
            state = 1
        out[i] = state
    return (pd.Series(out == 1, index=m.index), pd.Series(upper, index=m.index),
            pd.Series(lower, index=m.index))


def prep_variant(df: pd.DataFrame, variant: str) -> pd.DataFrame:
    base = bt.prep(df)                       # pivots, prev_hh etc. (and BASE bands)
    if variant == "BASE":
        return base
    m = df.resample("ME").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    o4 = (m.Open + m.High + m.Low + m.Close) / 4
    ema = o4.ewm(span=EMA_LEN, adjust=False).mean()
    ema[: EMA_LEN] = np.nan                  # need a warm-up like the SMA has
    if variant == "A":
        green, up, lo = bands_around(m, ema)
        monthly = pd.DataFrame({"basis": ema, "upperR": up, "lowerR": lo, "green": green})
    else:  # B
        rising = ema > ema.shift(1)
        monthly = pd.DataFrame({"basis": ema, "upperR": ema, "lowerR": ema, "green": rising})
    monthly = monthly.shift(1)
    out = df.copy()
    mm = monthly.reindex(df.index, method="ffill")
    for c in ["basis", "upperR", "lowerR", "green"]:
        out[c] = mm[c]
    out["halfw"] = out["upperR"] - out["basis"]
    out["lowerB"] = out["lowerR"]
    for c in ["pivot_conf", "prev_hh", "stoch_k_lagged"]:
        out[c] = base[c].reindex(out.index)
    out = out.dropna(subset=["basis", "upperR"])
    out["green"] = out["green"].astype(bool)
    return out


def spy_bounce_check(df):
    m = df.resample("ME").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    o4 = (m.Open + m.High + m.Low + m.Close) / 4
    ema = o4.ewm(span=EMA_LEN, adjust=False).mean()
    ema[: EMA_LEN] = np.nan
    rising = ema > ema.shift(1)
    eps, in_ep = [], False
    for i in range(EMA_LEN + 1, len(m)):
        touch = m.Low.iloc[i] <= ema.iloc[i]
        if touch and not in_ep:
            in_ep = True
            # broke = closed a month below the line AND the line turned down within 12 months
            w = slice(i, min(i + 13, len(m)))
            broke = bool((~rising.iloc[w]).any() and (m.Close.iloc[w] < ema.iloc[w]).any())
            eps.append((m.index[i].date(), "BREAK" if broke else "BOUNCE"))
        elif not touch and in_ep:
            in_ep = False
    nb = sum(1 for _, o in eps if o == "BOUNCE")
    print(f"\nSPY touches of the 20-month EMA line since {m.index[EMA_LEN].date()}: "
          f"{len(eps)} episodes, {nb} bounced, {len(eps)-nb} broke")
    print("  breaks: " + ", ".join(str(d) for d, o in eps if o == "BREAK"))
    print("  (Peter's claim: 20 episodes, 18 bounced, breaks 2000 and 2008)")


def main():
    variants = ["BASE", "A", "B"]
    trades = {v: [] for v in variants}
    spy_df = None
    for tk in bt.UNIVERSE:
        try:
            df = bt.fetch(tk)
        except Exception as e:
            print(f"{tk}: fetch error {e}", file=sys.stderr); continue
        if df is None or len(df) < bt.FVB_LEN * 5:
            continue
        if tk == "SPY":
            spy_df = df
        for v in variants:
            try:
                trs = bt.run(prep_variant(df, v), band="High", exit_mode="NBarHigh")
            except Exception as e:
                print(f"{tk} {v}: run error {e}", file=sys.stderr); continue
            for t in trs:
                trades[v].append((tk, t["entry_date"], t["exit_date"], float(t["ret"]), t["reason"]))

    # common period: from the latest first-entry among variants
    start = max(min(t[1] for t in trades[v]) for v in variants if trades[v])
    print(f"Common period from {start.date()} (so all three cover the same years)\n")
    names = {"BASE": "BASE  our 33-month FVB (live)", "A": "A     20-month EMA basis, same bands",
             "B": "B     touch 20-month EMA line, EMA-slope stop"}
    for v in variants:
        tr = [t for t in trades[v] if t[1] >= start]
        r = np.array([t[3] for t in tr])
        wins, losses = r[r > 0], r[r <= 0]
        pf = wins.sum() / -losses.sum() if len(losses) and losses.sum() < 0 else np.nan
        stops = sum(1 for t in tr if t[4] == "STOP")
        holds = np.array([(t[2] - t[1]).days / 7 for t in tr])
        pt = [(t[0], t[1], t[2], t[3], max((t[2] - t[1]).days / 7.0, 1 / 7)) for t in tr]
        eq, taken, _ = build_portfolio(pt, SLOTS, lambda a, b: False, CASH_R_WEEKLY)
        mt = metrics(eq)
        print(f"{names[v]}")
        print(f"   trades {len(r):5d}   win {100*(r>0).mean():5.1f}%   mean {100*r.mean():+6.1f}%   "
              f"median {100*np.median(r):+6.1f}%   PF {pf:4.2f}   stops {stops}   "
              f"median hold {np.median(holds):.0f} wks")
        print(f"   portfolio ({SLOTS} slots): CAGR {100*mt['cagr']:+.1f}%   max DD {100*mt['dd']:+.1f}%   "
              f"Sharpe {mt['sharpe']:.2f}   entries taken {taken}\n")

    if spy_df is None:
        spy_df = bt.fetch("SPY")
    spy_bounce_check(spy_df)


if __name__ == "__main__":
    main()
