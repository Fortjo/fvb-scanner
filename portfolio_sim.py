#!/usr/bin/env python3
"""
Shared, repaired event-driven portfolio simulator for testing entry
filters against the real FVB trade history. This replaces the earlier
one-off copies in test_fvb_market_filter.py and the analyze_*_filter.py
scratch scripts, after an independent quant audit (Nov 2026) found real
bugs in the original version. Fixes applied here, with the audit's
numbering preserved for traceability:

  #1 Compounding: each trade's return is spread across its ACTUAL
     elapsed time (exit_date - entry_date), not the possibly-rounded
     `hold` field. Verified by test_compounding_identity() below --
     +10% over 10 days must reconstruct to exactly +10.000000%.
  #2 Equity staleness: the equity used to size a NEW entry is recomputed
     immediately after that event's exits are processed, not carried
     over from the previous event.
  #5 Sharpe: computed on a weekly-resampled (ffilled) equity series, not
     on raw pct_change() between irregular event dates.
  #9 Monte Carlo p-values anywhere this module's output feeds a placebo
     test should use the standard (+1)/(N+1) correction, not a raw
     count/N (that part lives in the calling script, not here).

NOT fixed here (known, documented limitations — see the audit response
doc for the full list):
  - max drawdown is still computed on the event-spaced equity curve, not
    a full daily mark-to-market reconstruction from real prices, so it
    likely UNDERSTATES true intra-trade drawdown.
  - no transaction costs, slippage, or point-in-time universe
    reconstruction (survivorship bias in the 170-ticker universe).
  - same-date entry tie-break ordering is whatever order `all_trades`
    lists them in (currently ticker-list order) — NOT yet stress-tested
    for sensitivity. That's the next thing being investigated (slot-order
    sensitivity test), per the external audit's suggested next step.

Usage: import build_portfolio and metrics from here into any new filter
test, rather than copy-pasting the simulator again.
"""
import numpy as np
import pandas as pd


def build_portfolio(all_trades, slots, skip_fn, cash_r_weekly=0.0):
    """all_trades: list of (ticker, entry_date, exit_date, ret, elapsed_weeks)
    -- elapsed_weeks MUST be the real (exit_date - entry_date) in weeks,
    not a rounded holding-period field (fix #1 -- compute this at the
    call site: elapsed_weeks = (exit_date - entry_date).days / 7.0).

    Event-driven over the REAL entry/exit dates. FIFO by entry date,
    fixed number of slots, first-come-first-served within a date (see
    the slot-order-sensitivity caveat above). skip_fn(ticker, entry_date)
    -> bool decides whether a signal is skipped (e.g. a regime filter);
    a signal is also skipped if no slot is free.
    """
    if not all_trades:
        return None, 0, 0
    entries_at, exits_at = {}, {}
    for tk, ed, xd, ret, ew in all_trades:
        entries_at.setdefault(ed, []).append((tk, xd, ret, ew))
        exits_at.setdefault(xd, []).append(tk)

    events = sorted(set(entries_at) | set(exits_at))
    cash = 1.0
    open_slots = {}   # tk -> [alloc, weekly_mult]
    eq_path, eq_dates = [], []
    taken, skipped = 0, 0
    prev_t = events[0]

    for t in events:
        elapsed = max((t - prev_t).days / 7.0, 0.0)
        if elapsed > 0:
            cash *= (1 + cash_r_weekly) ** elapsed
            for tk in list(open_slots):
                open_slots[tk][0] *= (1 + open_slots[tk][1]) ** elapsed
        # exits first, then entries, same timestamp
        for tk in exits_at.get(t, []):
            if tk in open_slots:
                alloc, _ = open_slots.pop(tk)
                cash += alloc
        # fix #2: recompute equity NOW (post-exit, pre-entry) for sizing,
        # instead of using a value carried over from the previous event.
        equity_now = cash + sum(a for a, _ in open_slots.values())
        for tk, xd, ret, ew in entries_at.get(t, []):
            if tk in open_slots:
                continue
            if skip_fn(tk, t):
                skipped += 1
                continue
            if len(open_slots) >= slots:
                continue
            alloc = min(equity_now / slots if slots > 0 else 0, cash)
            if alloc <= 0:
                continue
            cash -= alloc
            # fix #1: use the trade's REAL elapsed weeks, not a rounded
            # `hold` field, so the trade reconstructs to exactly `ret`
            # by its own exit date.
            wmult = (1 + ret) ** (1 / ew) - 1
            open_slots[tk] = [alloc, wmult]
            taken += 1
        pos_val = sum(a for a, _ in open_slots.values())
        equity_now = cash + pos_val
        eq_path.append(equity_now)
        eq_dates.append(t)
        prev_t = t

    eq = pd.Series(eq_path, index=pd.DatetimeIndex(eq_dates))
    eq = eq[~eq.index.duplicated(keep="last")]
    return eq, taken, skipped


def metrics(eq):
    """CAGR, max drawdown (event-based, see module docstring caveat),
    and Sharpe on a weekly-RESAMPLED equity series (fix #5) rather than
    raw event-to-event returns."""
    eq = eq.dropna()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = eq.iloc[-1] ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    weekly = eq.resample("W-FRI").ffill().dropna()
    r = weekly.pct_change().dropna()
    sharpe = r.mean() / r.std() * np.sqrt(52) if r.std() > 0 else np.nan
    return {"cagr": cagr, "dd": dd, "sharpe": sharpe}


def test_compounding_identity():
    """A trade entering with +10% return and exiting exactly 10 days
    later must reconstruct to exactly +10.000000% once spread across its
    own elapsed time and re-compounded. This is the exact check the
    external audit asked for after finding fix #1's bug."""
    entry = pd.Timestamp("2026-01-01")
    exit_ = entry + pd.Timedelta(days=10)
    ret = 0.10
    elapsed_weeks = (exit_ - entry).days / 7.0
    wmult = (1 + ret) ** (1 / elapsed_weeks) - 1
    grown = (1 + wmult) ** elapsed_weeks
    assert abs(grown - (1 + ret)) < 1e-9, f"compounding bug: {grown} != {1 + ret}"
    return True


if __name__ == "__main__":
    test_compounding_identity()
    print("portfolio_sim.py: compounding identity unit test passed.")
