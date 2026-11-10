#!/usr/bin/env python3
from __future__ import annotations
"""
Finishes the market-timing filter validation that was left half-done.

Takes FVB's OWN real trade signals (same backtest_33fvb.run(), same config
as the live system — band="High", exit_mode="NBarHigh") and compares three
versions of the system:

  A) as-is, no filter
  B) gated by the market-timing filter: skip a NEW buy signal if the S&P
     is below its own 10-month average at the time, take it normally
     otherwise (existing open positions are never touched)
  C) EXPOSURE-MATCHED PLACEBO: skip the same NUMBER of new entries as B
     skipped, but chosen at RANDOM rather than by the regime flag, repeated
     many times to build a distribution

(C) is the missing check from last time. A market-timing filter that skips
entries will almost always show a smaller drawdown than "no filter" simply
because it has less money in the market on average — that's true even if
the TIMING of when it skips is worthless. The only way to tell if the
regime signal itself is doing real work (vs. just being "any excuse to be
out of the market some of the time") is to compare it against randomly
skipping the same number of entries. If B's drawdown improvement sits
clearly outside the distribution of C's random-skip improvements, the
timing is doing real work. If B lands inside that distribution, the filter
is not adding anything beyond its exposure reduction and isn't worth
keeping.

This run's own reported baseline (version A) is the one to treat as
authoritative going forward — it supersedes any earlier/partial numbers
from prior ad-hoc runs, since this is the one documented, single-script,
full-universe calculation.

APPROXIMATION, disclosed up front: backtest_33fvb.run() returns each
trade's total cost-adjusted return and hold length, not a full weekly
price path. To get a weekly equity curve for drawdown/Sharpe, each
trade's return is spread evenly across its hold length (geometric
interpolation) rather than marked-to-market on real weekly closes.
This is fine for comparing A vs B vs C (all three get the same treatment),
but don't read the absolute CAGR/drawdown numbers as precisely as the
live scan's own per-ticker stats — they're for the relative comparison.

Portfolio: a fixed number of slots (default 15, change with --slots),
first-come-first-served by entry date — if full, a signal is simply
skipped, same as it would be in practice. Cash earns a small assumed rate
between trades.

READ-ONLY RESEARCH — changes nothing live, no files touched except this
script's own printed output (and, if you pass --save-csv, a small CSV of
the random-control distribution for your own reference).

Usage (from your fvb-scanner folder, same place you ran the other scripts):
    python3 test_fvb_market_filter.py
    python3 test_fvb_market_filter.py --slots 10
    python3 test_fvb_market_filter.py --n-random 300
    python3 test_fvb_market_filter.py --synthetic   (quick smoke test, no network)
"""
import argparse
import sys
import numpy as np
import pandas as pd
import backtest_33fvb as bt


def spy_monthly_regime(synthetic):
    """SPY close above its own 10-month average, using only the prior
    COMPLETED month (shifted, like FVB's own basis) — reindexed/ffilled
    to weekly so it lines up with FVB's weekly trade dates."""
    if synthetic:
        close = bt.synthetic("SPY", n=1400, seed=999)["Close"]
    else:
        import yfinance as yf
        d = yf.download("SPY", start=bt.START, interval="1wk", auto_adjust=True, progress=False)
        if isinstance(d.columns, pd.MultiIndex):
            d.columns = d.columns.get_level_values(0)
        close = d["Close"]
    m = close.resample("ME").last()
    ok = (m > m.rolling(10).mean()).shift(1)
    return ok


def trades_for(tk, synthetic):
    try:
        df = bt.synthetic(tk) if synthetic else bt.fetch(tk)
    except Exception as e:
        print(f"{tk}: fetch error {e}", file=sys.stderr)
        return None
    if df is None or len(df) < bt.FVB_LEN * 5:
        return None
    try:
        p = bt.prep(df)
        trs = bt.run(p, band="High", exit_mode="NBarHigh")
    except Exception as e:
        print(f"{tk}: run error {e}", file=sys.stderr)
        return None
    return trs


def build_portfolio(all_trades, slots, skip_fn, cash_r_weekly=0.0):
    """all_trades: list of (ticker, entry_date, exit_date, ret, hold).
    Event-driven over the REAL entry/exit dates. skip_fn(ticker, entry_date)
    -> bool decides whether a signal is skipped (regime flag, or a random
    draw, depending on caller). FIFO by entry date; skips a signal if
    skip_fn says so, or if no slot is free."""
    if not all_trades:
        return None, 0, 0
    entries_at, exits_at = {}, {}
    for tk, ed, xd, ret, hold in all_trades:
        entries_at.setdefault(ed, []).append((tk, xd, ret, hold))
        exits_at.setdefault(xd, []).append(tk)

    events = sorted(set(entries_at) | set(exits_at))
    cash = 1.0
    open_slots = {}   # tk -> [alloc, weekly_mult]
    eq_path, eq_dates = [], []
    taken, skipped = 0, 0
    equity = 1.0
    prev_t = events[0]

    for t in events:
        elapsed_weeks = max((t - prev_t).days / 7.0, 0.0)
        if elapsed_weeks > 0:
            cash *= (1 + cash_r_weekly) ** elapsed_weeks
            for tk in list(open_slots):
                open_slots[tk][0] *= (1 + open_slots[tk][1]) ** elapsed_weeks
        for tk in exits_at.get(t, []):
            if tk in open_slots:
                alloc, _ = open_slots.pop(tk)
                cash += alloc
        for tk, xd, ret, hold in entries_at.get(t, []):
            if tk in open_slots:
                continue
            if skip_fn(tk, t):
                skipped += 1
                continue
            if len(open_slots) >= slots:
                continue
            alloc = min(equity / slots if slots > 0 else 0, cash)
            if alloc <= 0:
                continue
            cash -= alloc
            h = max(hold, 1)
            wmult = (1 + ret) ** (1 / h) - 1
            open_slots[tk] = [alloc, wmult]
            taken += 1
        pos_val = sum(a for a, _ in open_slots.values())
        equity = cash + pos_val
        eq_path.append(equity)
        eq_dates.append(t)
        prev_t = t

    eq = pd.Series(eq_path, index=pd.DatetimeIndex(eq_dates))
    eq = eq[~eq.index.duplicated(keep="last")]
    return eq, taken, skipped


def metrics(eq):
    eq = eq.dropna()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = eq.iloc[-1] ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    r = eq.pct_change().dropna()
    sharpe = r.mean() / r.std() * np.sqrt(52) if r.std() > 0 else np.nan
    return {"cagr": cagr, "dd": dd, "sharpe": sharpe}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slots", type=int, default=15)
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--cash-rate", type=float, default=0.0008)  # ~4%/yr weekly approx
    ap.add_argument("--n-random", type=int, default=200,
                     help="number of random-skip placebo runs for the exposure-matched control")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--save-csv", action="store_true",
                     help="save the random-control distribution to market_filter_placebo.csv")
    args = ap.parse_args()

    tickers = [f"SYN{i}" for i in range(40)] if args.synthetic else list(bt.UNIVERSE)
    regime_ok = spy_monthly_regime(args.synthetic)

    all_trades = []
    for tk in tickers:
        trs = trades_for(tk, args.synthetic)
        if trs is None:
            print(f"{tk}: skipped (no data / error)", file=sys.stderr)
            continue
        for t in trs:
            all_trades.append((tk, t["entry_date"], t["exit_date"], t["ret"], t["hold"]))
        print(f"{tk}: {len(trs)} trades", file=sys.stderr)

    n_tk = len(set(t[0] for t in all_trades))
    print(f"\n{'=' * 100}")
    print(f"FVB + MARKET-TIMING FILTER  ({n_tk} tickers, {len(all_trades)} raw trades, {args.slots} slots)")
    print(f"{'=' * 100}")
    print("Approximation: each trade's return is spread evenly across its hold length to build")
    print("a weekly equity curve. Read CAGR/DD/Sharpe here as relative (A vs B vs C), not as a")
    print("new precise headline number for FVB on its own.\n")

    # A) no filter
    eqA, takenA, _ = build_portfolio(all_trades, args.slots, skip_fn=lambda tk, t: False,
                                      cash_r_weekly=args.cash_rate)
    mA = metrics(eqA)
    print(f"  A) FVB as-is (no filter)")
    print(f"     CAGR {100*mA['cagr']:+.1f}%   max drawdown {100*mA['dd']:+.1f}%   Sharpe {mA['sharpe']:.2f}")
    print(f"     entries taken: {takenA}")
    print("     -> treat this CAGR/drawdown as the authoritative FVB baseline from here on.")

    # B) regime-gated
    def regime_skip(tk, t):
        return not bool(regime_ok.asof(t))
    eqB, takenB, skippedB = build_portfolio(all_trades, args.slots, skip_fn=regime_skip,
                                             cash_r_weekly=args.cash_rate)
    mB = metrics(eqB)
    print(f"\n  B) FVB + market-timing filter on new entries")
    print(f"     CAGR {100*mB['cagr']:+.1f}%   max drawdown {100*mB['dd']:+.1f}%   Sharpe {mB['sharpe']:.2f}")
    print(f"     entries taken: {takenB}   entries skipped by regime flag: {skippedB}")

    # C) exposure-matched random placebo: skip the SAME COUNT of entries,
    # chosen at random each run, repeated --n-random times.
    total_signals = takenA  # A never skips, so this is the full signal count
    skip_rate = skippedB / total_signals if total_signals else 0
    rng = np.random.default_rng(args.seed)
    placebo_dd, placebo_cagr = [], []
    for i in range(args.n_random):
        skip_mask = rng.random(len(all_trades)) < skip_rate
        skip_lookup = {}
        for (tk, ed, xd, ret, hold), skip in zip(all_trades, skip_mask):
            skip_lookup[(tk, ed)] = bool(skip)
        eqC, _, _ = build_portfolio(all_trades, args.slots,
                                     skip_fn=lambda tk, t: skip_lookup.get((tk, t), False),
                                     cash_r_weekly=args.cash_rate)
        if eqC is not None and len(eqC) > 2:
            mC = metrics(eqC)
            placebo_dd.append(mC["dd"])
            placebo_cagr.append(mC["cagr"])

    placebo_dd = np.array(placebo_dd)
    placebo_cagr = np.array(placebo_cagr)
    dd_improve_B = mA["dd"] - mB["dd"]           # positive = B improved (less negative dd)
    dd_improve_C = mA["dd"] - placebo_dd         # same, per random run
    pct_random_beats_B = 100 * np.mean(dd_improve_C >= dd_improve_B)

    print(f"\n  C) EXPOSURE-MATCHED PLACEBO: random skip of the same {100*skip_rate:.0f}% of "
          f"entries, {args.n_random} runs")
    print(f"     median CAGR {100*np.median(placebo_cagr):+.1f}%   "
          f"median max drawdown {100*np.median(placebo_dd):+.1f}%")
    print(f"     drawdown improvement vs baseline — regime filter: {100*dd_improve_B:+.1f}pts   "
          f"random-skip median: {100*np.median(dd_improve_C):+.1f}pts")
    print(f"     {pct_random_beats_B:.0f}% of random-skip runs improved drawdown AT LEAST as "
          f"much as the real regime filter did.")

    if args.save_csv:
        pd.DataFrame({"placebo_cagr": placebo_cagr, "placebo_dd": placebo_dd}).to_csv(
            "market_filter_placebo.csv", index=False)
        print("\n     saved per-run placebo distribution to market_filter_placebo.csv")

    print("\n" + "=" * 100)
    print("VERDICT")
    print("=" * 100)
    if pct_random_beats_B <= 10:
        print(f"The regime filter's drawdown improvement beats random-skip in ~{100-pct_random_beats_B:.0f}%")
        print("of random trials — this looks like real timing skill, not just 'being out of the")
        print("market sometimes.' Worth keeping as the advisory banner (already built, not yet live).")
    elif pct_random_beats_B <= 30:
        print(f"Borderline: only {pct_random_beats_B:.0f}% of random skips matched or beat it. Some signal,")
        print("but not a clean separation from noise — keep as advisory-only, don't lean on it hard.")
    else:
        print(f"{pct_random_beats_B:.0f}% of random skips did AT LEAST as well as the regime filter.")
        print("That means the drawdown improvement is mostly just 'being out of the market some of")
        print("the time,' not the market-regime TIMING doing real work. Not worth keeping as a")
        print("distinguishing edge over simply running fewer slots.")


if __name__ == "__main__":
    main()
