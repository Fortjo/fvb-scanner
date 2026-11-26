#!/usr/bin/env python3
"""
Backtest of Peter DiCarlo's "Silver Bullet" setup (chat, 2026-11-26).

What his posts/charts show (peter_smart_money_bands_observations.md):
  - DAILY chart; price pulls back into the daily Smart Money Zone -- the fib boxes of the
    last swing: "Institutional Buy Zone" 0.618-0.786 and "Smart Money Zone" 0.786-0.826
  - only while the long-term bull cycle is on: monthly AND weekly BX both positive
  - short-term target = prior swing high

Rules, fixed in advance (not tuned):
  Swing:  confirmed daily pivots, 10 bars left / 10 right (our SMZ module default).
          Leg = latest confirmed pivot high H and the latest confirmed pivot low L before it.
  Zone:   top = H - 0.618*(H-L), bottom = H - 0.826*(H-L).
  BX:     B-Xtrender short-term = RSI(EMA5(close) - EMA20(close), 5) - 50, on monthly and
          weekly bars; uses the last COMPLETED month/week (no look-ahead). Bull = both > 0.
  Entry:  first day after H is confirmed where the low touches the zone top, the open is
          still above L, and BX is bull. Fill at min(open, zone top). One entry per leg.
  Exit:   target H (fill max(open, H) on gaps); stop when a daily close is below L.
  Cost:   0.1% round trip.

Controls:
  NOBX    same rules without the BX filter (does the bull-cycle filter matter?)
  RANDOM  entries on random bull-cycle days, each copying the target/stop DISTANCES of a
          real Silver Bullet trade (same reward:risk geometry) -- does the zone timing add
          anything beyond the geometry?
  Portfolio level (portfolio_sim, 15 slots) for SB vs our 33-month FVB baseline.
Read-only research.
"""
from __future__ import annotations
import sys
import numpy as np
import pandas as pd
import yfinance as yf
import backtest_33fvb as bt
from portfolio_sim import build_portfolio, metrics

PIV_L = PIV_R = 10
RT_COST = 0.001
SLOTS = 15
CASH_R_WEEKLY = 0.0008
SEED = 7


def rsi(s: pd.Series, n: int) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def bx(close: pd.Series) -> pd.Series:
    spread = close.ewm(span=5, adjust=False).mean() - close.ewm(span=20, adjust=False).mean()
    return rsi(spread, 5) - 50


def bull_filter(d: pd.DataFrame) -> pd.Series:
    m = bx(d["Close"].resample("ME").last()).shift(1)       # last completed month
    w = bx(d["Close"].resample("W-FRI").last()).shift(1)    # last completed week
    mm = m.reindex(d.index, method="ffill")
    ww = w.reindex(d.index, method="ffill")
    return (mm > 0) & (ww > 0)


def run_sb(d: pd.DataFrame, bull: pd.Series, use_bx: bool):
    o, h, l, c = (d[k].values for k in ("Open", "High", "Low", "Close"))
    idx, n = d.index, len(d)
    bullv = bull.values
    piv_hi_conf = {}  # confirmation bar -> (pivot idx, price)
    piv_lo_conf = {}
    for i in range(PIV_L, n - PIV_R):
        wh = h[i - PIV_L:i + PIV_R + 1]
        wl = l[i - PIV_L:i + PIV_R + 1]
        if h[i] == wh.max() and (wh == h[i]).sum() == 1:
            piv_hi_conf[i + PIV_R] = (i, h[i])
        if l[i] == wl.min() and (wl == l[i]).sum() == 1:
            piv_lo_conf[i + PIV_R] = (i, l[i])
    last_low = None
    leg = None            # (L, H, top, bottom)
    armed = False
    pos = None            # (entry_i, entry_px, L, H)
    trades = []
    for i in range(n):
        # exits first (from the bar after entry)
        if pos is not None and i > pos[0]:
            ei, epx, L, H = pos
            xpx = None
            if h[i] >= H:
                xpx, why = max(o[i], H), "TP"
            elif c[i] < L:
                xpx, why = c[i], "STOP"
            if xpx is not None:
                trades.append({"entry_date": idx[ei], "exit_date": idx[i],
                               "ret": xpx / epx - 1 - RT_COST, "reason": why,
                               "tp_dist": H / epx - 1, "sl_dist": L / epx - 1})
                pos = None
        # entry check uses the leg known before today
        if pos is None and armed and leg is not None and i > 0:
            L, H, top, bot = leg
            if c[i - 1] < L:
                armed = False                      # leg broken before any touch
            elif l[i] <= top and o[i] > L and (bullv[i] or not use_bx):
                epx = min(o[i], top)
                pos = (i, epx, L, H)
                armed = False
        # update pivots confirmed at the close of today (usable from tomorrow)
        if i in piv_lo_conf:
            last_low = piv_lo_conf[i]
        if i in piv_hi_conf:
            hi_i, H = piv_hi_conf[i]
            if last_low is not None and last_low[0] < hi_i and last_low[1] < H:
                L = last_low[1]
                rng = H - L
                leg = (L, H, H - 0.618 * rng, H - 0.826 * rng)
                armed = True
    return trades


def run_random(d, bull, templates, rng, n_trades):
    """Random bull-cycle entry days, each with a real trade's target/stop distances."""
    o, h, l, c = (d[k].values for k in ("Open", "High", "Low", "Close"))
    idx = d.index
    days = np.where(bull.values)[0]
    days = days[days < len(d) - 2]
    out = []
    if len(days) == 0 or not templates:
        return out
    for _ in range(n_trades):
        i = int(rng.choice(days))
        tp, sl = templates[int(rng.integers(len(templates)))]
        epx = c[i]
        H, L = epx * (1 + tp), epx * (1 + sl)
        for j in range(i + 1, len(d)):
            if h[j] >= H:
                out.append({"entry_date": idx[i], "exit_date": idx[j], "ret": max(o[j], H) / epx - 1 - RT_COST})
                break
            if c[j] < L:
                out.append({"entry_date": idx[i], "exit_date": idx[j], "ret": c[j] / epx - 1 - RT_COST})
                break
    return out


def summary(name, tr):
    if not tr:
        print(f"{name}: no trades"); return
    r = np.array([t["ret"] for t in tr])
    days = np.array([(t["exit_date"] - t["entry_date"]).days for t in tr])
    w, ls = r[r > 0], r[r <= 0]
    pf = w.sum() / -ls.sum() if ls.sum() < 0 else np.nan
    print(f"{name:42s} trades {len(r):5d}  win {100*(r>0).mean():5.1f}%  mean {100*r.mean():+6.2f}%  "
          f"median {100*np.median(r):+6.2f}%  PF {pf:4.2f}  median hold {np.median(days):4.0f} days  "
          f"mean per 30 days held {100*r.mean()/max(days.mean(),1)*30:+5.2f}%")


def main():
    rng = np.random.default_rng(SEED)
    sb, nobx, rand = [], [], []
    per_ticker = {}
    for tk in bt.UNIVERSE:
        try:
            d = yf.download(tk, start="1995-01-01", interval="1d", auto_adjust=True, progress=False)
            if isinstance(d.columns, pd.MultiIndex):
                d.columns = d.columns.get_level_values(0)
            d = d[["Open", "High", "Low", "Close"]].dropna()
            if len(d) < 800:
                continue
            bull = bull_filter(d).fillna(False)
            t1 = run_sb(d, bull, True)
            t2 = run_sb(d, bull, False)
            templates = [(t["tp_dist"], t["sl_dist"]) for t in t1]
            t3 = run_random(d, bull, templates, rng, len(t1))
        except Exception as e:
            print(f"{tk}: error {e}", file=sys.stderr); continue
        for t in t1: t["tk"] = tk
        for t in t2: t["tk"] = tk
        for t in t3: t["tk"] = tk
        sb += t1; nobx += t2; rand += t3
        per_ticker[tk] = len(t1)

    print(f"Universe: {len(per_ticker)} tickers (S&P100 + NDX100), daily data from 1995\n")
    summary("SILVER BULLET (zone + monthly&weekly BX)", sb)
    summary("  control NOBX (zone, no BX filter)", nobx)
    summary("  control RANDOM (bull days, same geometry)", rand)
    if sb:
        stops = sum(1 for t in sb if t["reason"] == "STOP")
        print(f"\nSilver Bullet: {len(sb)-stops} hit target, {stops} stopped out; "
              f"median target distance {100*np.median([t['tp_dist'] for t in sb]):.1f}%, "
              f"median stop distance {100*np.median([t['sl_dist'] for t in sb]):.1f}%")
        yrs = pd.Series([t["exit_date"].year for t in sb])
        r = pd.Series([t["ret"] for t in sb])
        by = pd.DataFrame({"y": yrs, "r": r}).groupby("y").r.agg(["count", "mean"])
        print("Per year (trades / mean %): " + ", ".join(f"{y}:{int(a)}/{100*b:+.1f}" for y, (a, b) in by.iterrows()))

    def port(tr, label):
        pt = [(t["tk"], t["entry_date"], t["exit_date"], t["ret"],
               max((t["exit_date"] - t["entry_date"]).days / 7.0, 1 / 7)) for t in tr]
        eq, taken, _ = build_portfolio(pt, SLOTS, lambda a, b: False, CASH_R_WEEKLY)
        m = metrics(eq)
        print(f"  {label:38s} CAGR {100*m['cagr']:+5.1f}%  max DD {100*m['dd']:+6.1f}%  "
              f"Sharpe {m['sharpe']:.2f}  entries {taken}  ({eq.index[0].date()} -> {eq.index[-1].date()})")

    print(f"\nPORTFOLIO ({SLOTS} slots, same simulator as all earlier tests)")
    port(sb, "Silver Bullet")
    port(nobx, "control NOBX")
    port(rand, "control RANDOM")
    print("  (our 33-month FVB on the same universe: CAGR +14.4%, max DD -22.6%, "
          "from test_ema20_band_system.py)")


if __name__ == "__main__":
    main()
