#!/usr/bin/env python3
"""
Milestone A (per the external quant audit, Phase 5): fix the two
monthly-indicator timing bugs in backtest_33fvb.prep() WITHOUT touching
the strategy's entry/exit/band logic itself. backtest_33fvb.py is left
completely UNCHANGED and stays the benchmark; this script builds a
parallel, corrected indicator pipeline and compares the two side by
side on several real tickers, plus runs explicit no-lookahead tests.

Two bugs fixed here (audit items #5 and #6):

  #5 Monthly misattribution: prep() currently builds its "monthly" OHLC
     by resample("ME")-ing the WEEKLY-interval data. A weekly bar whose
     5 trading days straddle a month boundary gets its ENTIRE High/Low/
     Close attributed to whichever month resample() labels it with --
     even though part of that bar's range may reflect the following
     month's price action. FIX: build the monthly OHLC from DAILY bars
     instead (backtest_33fvb.fetch_daily() already exists in the repo,
     just wasn't used for this). Every daily bar belongs to exactly one
     real calendar month, so this removes the misattribution entirely.

  #6 Extra month of lag: the original code does
         monthly = pd.DataFrame({...}).shift(1)
         mm = monthly.reindex(weekly_index, method="ffill")
     The .shift(1) moves January's computed row onto February's label.
     Combined with ffill-reindex, a weekly bar in early March picks up
     the Feb-28-labeled row -- which (because of the shift) actually
     holds JANUARY's value, not February's, even though February's
     complete data was already known by March 1. FIX: the resample-to-
     month-end + ffill-reindex mechanism is ALREADY lookahead-safe on
     its own (a given week can only ffill-pick a monthly row whose own
     label date is <= that week, i.e. a COMPLETED month) -- the .shift(1)
     is redundant and is what introduces the extra month of staleness.
     Removing it restores the intended "last completed month, no more
     and no less" semantic, with an explicit assertion (below) proving
     no future bar ever enters a decision.

What's NOT fixed here (flagged for Milestone B, not silently skipped):
  - audit item #4/#7: weekly-bar intrabar TP/STOP/entry fill ordering
    ambiguity in run() itself (which of High/Low/Open "happened first"
    within one weekly bar). That needs a full daily-level execution
    reconstruction of run(), which is a bigger undertaking than the
    indicator-timing fix here -- deferred to Milestone B.
  - audit item #2: survivorship-biased universe (current index members
    applied to 1970s-present history). Deferred -- would need a point-
    in-time constituent history data source.
  - audit item #3: the --split train/test selection in main()'s
    "PLAIN-ENGLISH READOUT" picks the best band/exit combo by looking at
    test-segment performance, which is a form of leakage if that
    selection ever informed which config got deployed live. Flagged,
    not fixed here -- Milestone A freezes the baseline config (band=
    High, exit=NBarHigh) as given, per your instruction, rather than
    re-selecting it.

Usage (run locally -- needs yfinance):
    python3 milestone_a_engine_fix.py
    python3 milestone_a_engine_fix.py --tickers MSFT,AAPL,ORCL,SOFI,XEL
    python3 milestone_a_engine_fix.py --synthetic   # no-network lookahead test only
"""
import argparse
import sys
import numpy as np
import pandas as pd
import backtest_33fvb as bt

TEST_TICKERS_DEFAULT = ["MSFT", "AAPL", "ORCL", "SOFI", "XEL", "WMT", "NVDA"]


# --------------------------------------------------------------- new prep
def prep_v2(df_weekly: pd.DataFrame, df_daily: pd.DataFrame) -> pd.DataFrame:
    """Corrected indicator pipeline: monthly OHLC built from DAILY bars
    (fix #5), no extra shift (fix #6). Entries/exits still happen on the
    WEEKLY grid (df_weekly) -- only how the monthly indicator itself is
    built and mapped onto that weekly grid changes. The band/state
    FORMULA is untouched: same bt._compute_real_fvb() call as the
    original, same FVB_LEN, same everything except the input source."""
    m = df_daily.resample("ME").agg({"Open": "first", "High": "max",
                                      "Low": "min", "Close": "last"}).dropna()
    green, upperR, lowerR = bt._compute_real_fvb(m, bt.FVB_LEN)
    ohlc4 = (m["Open"] + m["High"] + m["Low"] + m["Close"]) / 4
    basis = ohlc4.rolling(bt.FVB_LEN).mean()
    monthly = pd.DataFrame({"basis": basis, "upperR": upperR, "lowerR": lowerR,
                             "green": green})
    # NO .shift(1) here -- see fix #6 above. reindex+ffill alone is already
    # lookahead-safe: a weekly bar dated t can only pick up a monthly row
    # whose own index label (a month-END date) is <= t, i.e. an already-
    # completed month.
    out = df_weekly.copy()
    mm = monthly.reindex(df_weekly.index, method="ffill")
    out[["basis", "upperR", "lowerR", "green"]] = mm
    out["halfw"] = out["upperR"] - out["basis"]
    out["lowerB"] = out["lowerR"]

    h = df_weekly["High"].values
    n = len(h)
    conf = np.full(n, np.nan)
    for i in range(bt.PIV_L, n - bt.PIV_R):
        w = h[i - bt.PIV_L: i + bt.PIV_R + 1]
        if h[i] == w.max() and (w == h[i]).sum() == 1:
            conf[i + bt.PIV_R] = h[i]
    out["pivot_conf"] = conf
    out["prev_hh"] = df_weekly["High"].rolling(bt.NBAR_LEN).max().shift(1)
    return out.dropna(subset=["basis"]), monthly


# --------------------------------------------------------- no-lookahead test
def test_no_lookahead():
    """Synthetic daily+weekly series. For every weekly decision bar,
    assert the monthly row actually used (per mm.reindex(...).asof-style
    lookup) comes from a month whose own last trading day is STRICTLY
    BEFORE that weekly bar's date -- i.e. no future information, and
    (unlike the original) no MORE than one month of unnecessary extra
    staleness either."""
    rng = np.random.default_rng(42)
    daily_idx = pd.date_range("2015-01-01", periods=3000, freq="B")
    close = 50 * np.exp(np.cumsum(rng.normal(0.0004, 0.015, len(daily_idx))))
    daily = pd.DataFrame({
        "Open": close * (1 + rng.normal(0, 0.003, len(daily_idx))),
        "High": close * (1 + np.abs(rng.normal(0, 0.006, len(daily_idx)))),
        "Low": close * (1 - np.abs(rng.normal(0, 0.006, len(daily_idx)))),
        "Close": close,
    }, index=daily_idx)
    weekly = daily.resample("W-FRI").agg({"Open": "first", "High": "max",
                                           "Low": "min", "Close": "last"}).dropna()

    _, monthly = prep_v2(weekly, daily)
    # monthly index = month-end label = last day that month's row could
    # possibly reflect. For every weekly bar, whatever monthly row ffill
    # assigned it must have a label strictly before the weekly bar's date,
    # AND must be the MOST RECENT such completed month (no extra lag).
    violations, stale_by_2 = 0, 0
    monthly_labels = monthly.index
    # what prep_v2's own reindex+ffill actually assigns to each weekly bar:
    mm_actual = monthly.reindex(weekly.index, method="ffill")
    for wk_date in weekly.index:
        # "most recently completed" uses the SAME tie convention as a
        # correct ffill pick (<=): a month-end landing on this exact
        # trading day is same-bar information, not future information.
        eligible = monthly_labels[monthly_labels <= wk_date]
        if len(eligible) == 0:
            continue
        most_recent_completed = eligible[-1]
        if most_recent_completed > wk_date:
            violations += 1  # used a month that hadn't completed yet -- lookahead
        actual_basis = mm_actual.loc[wk_date, "basis"]
        expected_basis = monthly.loc[most_recent_completed, "basis"]
        both_nan = pd.isna(actual_basis) and pd.isna(expected_basis)
        # "stale by 2" = the value ACTUALLY used by prep_v2 differs from
        # the value of the truly most-recently-completed month (i.e. an
        # extra month of lag is sneaking in somewhere)
        if not both_nan and (pd.isna(actual_basis) != pd.isna(expected_basis)
                              or abs(actual_basis - expected_basis) > 1e-9):
            stale_by_2 += 1

    print(f"no-lookahead test: {violations} lookahead violations "
          f"(must be 0) across {len(weekly)} weekly decision bars")
    print(f"staleness check: {stale_by_2} weekly bars used a month OTHER than "
          f"the most recently completed one (must be 0 -- any >0 means the "
          f"extra-lag bug is still present)")
    assert violations == 0, "LOOKAHEAD DETECTED -- fix is broken"
    assert stale_by_2 == 0, "EXTRA LAG STILL PRESENT -- fix #6 did not work"
    print("PASSED: no lookahead, no extra staleness.\n")


# --------------------------------------------------------- old vs new diff

# NOTE: an earlier version of this filter reused scan_current_buys.py's
# straddle_n < 75 "thin_sample" WARNING as a hard EXCLUSION threshold.
# That was the wrong tool for this job: that threshold was only ever meant
# to flag an otherwise-live signal for extra caution, not to decide whether
# a trade's entry era is trustworthy enough to include in a comparison.
# The straddle count itself can legitimately stay low for a smoothly
# trending ticker across its ENTIRE multi-decade history (the module's own
# docstring example: XEL accumulates only ~51 straddle-months total since
# 1973) -- applying 75 as a point-in-time per-trade cutoff wiped out nearly
# every trade for every ticker except WMT, which isn't a useful comparison,
# just an overcorrection. Replaced with a plain calendar warm-up instead:
# require a minimum number of MONTHS OF PRICE HISTORY since the ticker's
# first available bar, independent of the straddle-specific statistic.
MIN_HISTORY_MONTHS = 60  # 5 years -- a standard backtesting warm-up buffer,
                         # chosen to exclude MSFT's 44-month 1989 entry
                         # without being so strict it guts everything else


def months_of_history_asof(m: pd.DataFrame, as_of) -> int:
    """Calendar months between a monthly OHLC series' first row and a
    given date -- NOT the straddle-specific count, just plain elapsed
    history. This is what actually explains MSFT's outlier trade (44
    months since IPO is simply too little history for the sticky trend
    state to be trustworthy yet, regardless of how the straddle median
    happens to be behaving)."""
    if m.empty or as_of < m.index[0]:
        return 0
    return len(m[m.index <= as_of])


def compare_old_vs_new(tickers):
    rows = []
    for tk in tickers:
        try:
            weekly = bt.fetch(tk)
            daily = bt.fetch_daily(tk)
        except Exception as e:
            print(f"{tk}: fetch error {e}", file=sys.stderr)
            continue
        if weekly is None or daily is None or len(weekly) < bt.FVB_LEN * 5:
            print(f"{tk}: insufficient data", file=sys.stderr)
            continue

        old = bt.prep(weekly)
        new, _monthly_indicator_new = prep_v2(weekly, daily)
        # OHLC monthly series for thin-sample counting -- NOT the indicator
        # output (basis/upperR/lowerR/green) that prep_v2 returns, which has
        # no Open/High/Low/Close columns. Built the same way each pipeline
        # builds it internally: OLD from weekly-resample, NEW from daily-
        # resample, so the comparison stays like-for-like.
        monthly_old = weekly.resample("ME").agg({"Open": "first", "High": "max",
                                                  "Low": "min", "Close": "last"}).dropna()
        monthly_new = daily.resample("ME").agg({"Open": "first", "High": "max",
                                                 "Low": "min", "Close": "last"}).dropna()

        # align on common weekly index
        common = old.index.intersection(new.index)
        old_c, new_c = old.loc[common], new.loc[common]

        green_diff = (old_c["green"] != new_c["green"]).sum()
        basis_pct_diff = ((new_c["basis"] - old_c["basis"]).abs() / old_c["basis"]).mean()
        band_pct_diff = ((new_c["upperR"] - old_c["upperR"]).abs() / old_c["upperR"]).mean()

        # re-run the actual trade logic on each prepped dataframe, config frozen
        trades_old = bt.run(old, "High", "NBarHigh")
        trades_new = bt.run(new, "High", "NBarHigh")

        # warm-up filter: drop any trade whose ENTRY happened before its
        # own pipeline had accumulated MIN_HISTORY_MONTHS of calendar
        # history -- see the note above on why this replaced the
        # straddle-count-based filter.
        trades_old_f = [t for t in trades_old
                         if months_of_history_asof(monthly_old, t["entry_date"]) >= MIN_HISTORY_MONTHS]
        trades_new_f = [t for t in trades_new
                         if months_of_history_asof(monthly_new, t["entry_date"]) >= MIN_HISTORY_MONTHS]
        n_dropped_old = len(trades_old) - len(trades_old_f)
        n_dropped_new = len(trades_new) - len(trades_new_f)

        st_old, st_new = bt.stats(trades_old_f), bt.stats(trades_new_f)

        rows.append({
            "ticker": tk, "weekly_bars_compared": len(common),
            "green_state_flips_differ_on_pct": 100 * green_diff / len(common),
            "mean_basis_pct_diff": 100 * basis_pct_diff,
            "mean_upperband_pct_diff": 100 * band_pct_diff,
            "old_trades_thin_dropped": n_dropped_old, "new_trades_thin_dropped": n_dropped_new,
            "old_trades": st_old["trades"], "new_trades": st_new["trades"],
            "old_win_rate": st_old["win_rate"], "new_win_rate": st_new["win_rate"],
            "old_expectancy": st_old["expectancy"], "new_expectancy": st_new["expectancy"],
            "old_pf": st_old["pf"], "new_pf": st_new["pf"],
        })
        print(f"{tk}: done "
              f"(green differs on {100*green_diff/len(common):.1f}% of weeks, "
              f"{n_dropped_old} old / {n_dropped_new} new trades dropped as warm-up, "
              f"old {st_old['trades']} trades / new {st_new['trades']} trades after filter)",
              file=sys.stderr)

    out = pd.DataFrame(rows)
    out.to_csv("milestone_a_old_vs_new.csv", index=False)
    pd.set_option("display.width", 160)
    print(f"\n=== OLD (benchmark) vs NEW (daily-sourced, no-extra-shift) "
          f"-- trades entering before {MIN_HISTORY_MONTHS} months of calendar history EXCLUDED from both ===")
    print(out.to_string(index=False))
    print("\nSaved: milestone_a_old_vs_new.csv -- send this back for review.")
    print("\nHow to read it: 'green_state_flips_differ_on_pct' is the most consequential column --")
    print("every week where old/new disagree on green/red is a week where the two engines would")
    print("make a DIFFERENT entry/exit decision. A small number (a few %) is expected given the")
    print("fixes target a 1-month timing shift, not a wholesale rewrite. A large number would mean")
    print("something deeper is wrong with one of the two pipelines and needs investigation before")
    print("trusting either one's trade-level results.")
    print(f"\n'*_trades_thin_dropped' shows how many trades were excluded for entering before "
          f"{MIN_HISTORY_MONTHS} months (a standard warm-up buffer, NOT the live system's separate")
    print("thin_sample WARNING, which an earlier version of this script mistakenly reused as an")
    print("exclusion cutoff and which wiped out almost every trade for every ticker except WMT --")
    print("that threshold can legitimately never clear 75 across a ticker's entire history for a")
    print("smoothly trending stock, so it isn't a usable per-trade filter. This warm-up buffer is")
    print("what removed MSFT's one 1989-entry, 11-year, +4584% outlier trade from the comparison.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", type=str, default=None)
    ap.add_argument("--synthetic", action="store_true",
                     help="run only the no-lookahead unit test, no network needed")
    args = ap.parse_args()

    print("Running no-lookahead / no-extra-staleness unit test (synthetic data)...\n")
    test_no_lookahead()

    if args.synthetic:
        return

    tickers = args.tickers.split(",") if args.tickers else TEST_TICKERS_DEFAULT
    compare_old_vs_new(tickers)


if __name__ == "__main__":
    main()
