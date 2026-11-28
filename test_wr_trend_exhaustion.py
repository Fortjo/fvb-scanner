#!/usr/bin/env python3
"""
Backtest of "%R Trend Exhaustion" (by upslidedown on TradingView), as described in
Michael Whitman's YouTube video (2026-11-27), shared by Forty for independent testing.

Rules, as stated in the video (not tuned by us):
  Two Williams %R lines, both daily chart, default settings:
    fast = SMA(WilliamsR(21), 7)    looks back ~1 month
    slow = SMA(WilliamsR(112), 3)   looks back ~5 months
  Exhaustion threshold 20 -> zones at -80 (bottom) / -20 (top).
  "Bottom zone" = BOTH lines <= -80 at once (squares print here, warning only).
  Up arrow = bottom zone ENDS (was true last bar, false this bar) -> buy next day's open.
  Down arrow = top zone ENDS (symmetric) -> sell next day's open. No fixed stop/target --
  the exit zone IS the exit. Long only, one position at a time, skip signals while in a trade.
  Claimed result (their own 20-stock sample, 2014-Nov 2026, $10k/trade): ~$160k / ~200
  trades, 80%+ win rate, never lost more than 5 in a row, no fixed stop.

What we're checking, same standard as every other indicator tested in this project:
  1. Does the real rule (BOTH lines must agree) actually beat a RANDOM control sized to
     the same hold-length distribution -- i.e. is there real timing edge, or just a
     slow-moving trend-following exit that any random entry would also ride?
  2. Does requiring both lines actually help vs the "mistake" the video itself warns
     about -- entering on the FAST line alone? This is a direct, checkable claim.
  3. Portfolio-level simulation (15 slots, our standard engine) vs our live 33-month FVB.
Read-only research, no changes to the live scanner/indicator.
"""
from __future__ import annotations
import sys
import numpy as np
import pandas as pd
import yfinance as yf
import backtest_33fvb as bt
from portfolio_sim import build_portfolio, metrics

RT_COST = 0.001
SLOTS = 15
CASH_R_WEEKLY = 0.0008
SEED = 11


def wr(d: pd.DataFrame, length: int) -> pd.Series:
    hh = d["High"].rolling(length).max()
    ll = d["Low"].rolling(length).min()
    return -100 * (hh - d["Close"]) / (hh - ll)


def lines(d: pd.DataFrame):
    fast = wr(d, 21).rolling(7).mean()
    slow = wr(d, 112).rolling(3).mean()
    return fast, slow


def run_system(d: pd.DataFrame, fast: pd.Series, slow: pd.Series, both_required: bool):
    o, c = d["Open"].values, d["Close"].values
    idx = d.index
    n = len(d)
    f, s = fast.values, slow.values
    if both_required:
        bot = (f <= -80) & (s <= -80)
        top = (f >= -20) & (s >= -20)
    else:
        # the "mistake" the video warns about: fast line alone
        bot = (f <= -80)
        top = (f >= -20)
    trades = []
    in_pos = False
    entry_i = -1
    entry_px = np.nan
    for i in range(1, n - 1):
        if np.isnan(f[i]) or np.isnan(s[i]):
            continue
        up = bot[i - 1] and not bot[i]
        dn = top[i - 1] and not top[i]
        if in_pos and dn:
            xpx = o[i + 1]
            trades.append({"entry_date": idx[entry_i], "exit_date": idx[i + 1],
                            "ret": xpx / entry_px - 1 - RT_COST,
                            "hold": (idx[i + 1] - idx[entry_i]).days})
            in_pos = False
        elif (not in_pos) and up:
            entry_px, entry_i, in_pos = o[i + 1], i + 1, True
    return trades


def run_random(d: pd.DataFrame, hold_days_pool: list, rng: np.random.Generator, n_trades: int):
    """Random entry days, each held for a hold-length drawn from the REAL system's own
    hold-time distribution on this ticker's universe -- isolates timing skill from the
    simple fact that a slow trend-following exit rides out long winners."""
    o, c = d["Open"].values, d["Close"].values
    idx = d.index
    n = len(d)
    out = []
    if n < 50 or not hold_days_pool:
        return out
    for _ in range(n_trades):
        i = int(rng.integers(1, n - 2))
        hold = int(rng.choice(hold_days_pool))
        j = min(i + max(hold, 1), n - 1)
        if j <= i:
            continue
        out.append({"entry_date": idx[i], "exit_date": idx[j],
                    "ret": o[j] / o[i] - 1 - RT_COST, "hold": (idx[j] - idx[i]).days})
    return out


def summary(name, tr):
    if not tr:
        print(f"{name}: no trades"); return
    r = np.array([t["ret"] for t in tr])
    hold = np.array([t["hold"] for t in tr])
    w, ls = r[r > 0], r[r <= 0]
    pf = w.sum() / -ls.sum() if ls.sum() < 0 else np.nan
    # longest losing streak (chronological, pooled across tickers by exit date)
    order = np.argsort([t["exit_date"] for t in tr])
    seq = r[order] <= 0
    longest = cur = 0
    for v in seq:
        cur = cur + 1 if v else 0
        longest = max(longest, cur)
    print(f"{name:34s} trades {len(r):5d}  win {100*(r>0).mean():5.1f}%  mean {100*r.mean():+6.2f}%  "
          f"median {100*np.median(r):+6.2f}%  PF {pf:4.2f}  median hold {np.median(hold):4.0f}d  "
          f"longest losing streak {longest}")


def main():
    rng = np.random.default_rng(SEED)
    real, single, rand = [], [], []
    per_ticker = {}
    for tk in bt.UNIVERSE:
        try:
            d = yf.download(tk, start="1995-01-01", interval="1d", auto_adjust=True, progress=False)
            if isinstance(d.columns, pd.MultiIndex):
                d.columns = d.columns.get_level_values(0)
            d = d[["Open", "High", "Low", "Close"]].dropna()
            if len(d) < 400:
                continue
            fast, slow = lines(d)
            t1 = run_system(d, fast, slow, True)
            t2 = run_system(d, fast, slow, False)
        except Exception as e:
            print(f"{tk}: error {e}", file=sys.stderr); continue
        for t in t1: t["tk"] = tk
        for t in t2: t["tk"] = tk
        real += t1; single += t2
        per_ticker[tk] = (len(t1), d)

    hold_pool = [t["hold"] for t in real]
    for tk, (n1, d) in per_ticker.items():
        t3 = run_random(d, hold_pool, rng, n1)
        for t in t3: t["tk"] = tk
        rand += t3

    print(f"Universe: {len(per_ticker)} tickers, daily data from 1995, as far back as available\n")
    summary("REAL (both lines required, as described)", real)
    summary("  control SINGLE-LINE (fast only, the 'mistake')", single)
    summary("  control RANDOM (random entries, real hold lengths)", rand)

    if real:
        yrs = pd.Series([t["exit_date"].year for t in real])
        r = pd.Series([t["ret"] for t in real])
        by = pd.DataFrame({"y": yrs, "r": r}).groupby("y").r.agg(["count", "mean"])
        print("\nPer year (trades / mean %): " + ", ".join(
            f"{y}:{int(a)}/{100*b:+.1f}" for y, (a, b) in by.iterrows()))
        last12 = r[pd.to_datetime([t["exit_date"] for t in real]) >= pd.Timestamp.now() - pd.Timedelta(days=365)]
        print(f"Last 12 months: {len(last12)} trades, mean {100*last12.mean():+.2f}%" if len(last12) else
              "Last 12 months: no closed trades")

    def port(tr, label):
        if not tr:
            print(f"  {label}: no trades for portfolio sim"); return
        pt = [(t["tk"], t["entry_date"], t["exit_date"], t["ret"],
               max(t["hold"] / 7.0, 1 / 7)) for t in tr]
        eq, taken, _ = build_portfolio(pt, SLOTS, lambda a, b: False, CASH_R_WEEKLY)
        m = metrics(eq)
        print(f"  {label:34s} CAGR {100*m['cagr']:+5.1f}%  max DD {100*m['dd']:+6.1f}%  "
              f"Sharpe {m['sharpe']:.2f}  entries {taken}  ({eq.index[0].date()} -> {eq.index[-1].date()})")

    print(f"\nPORTFOLIO ({SLOTS} slots, same simulator as all earlier tests)")
    port(real, "REAL (both lines)")
    port(single, "control SINGLE-LINE")
    port(rand, "control RANDOM")
    print("  (our 33-month FVB on the same universe: CAGR +14.4%, max DD -22.6%, "
          "from test_ema20_band_system.py)")


if __name__ == "__main__":
    main()
