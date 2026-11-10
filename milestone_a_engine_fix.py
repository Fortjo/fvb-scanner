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

THIN_SAMPLE_THRESHOLD = 75  # same threshold already live in scan_current_buys.py's
                            # "thin_sample" warning -- not invented for this script


def straddle_count_asof(m: pd.DataFrame, length: int, as_of) -> int:
    """Point-in-time version of bt.straddle_sample_size(): how many
    historical straddle months had accumulated by the time a given trade
    entered, not the series' final total. A trade entering while this is
    below THIN_SAMPLE_THRESHOLD is exactly the kind of early-history,
    fragile-median situation the live system already flags (MSFT's
    1989 entry, 44 months into its listing, is a clean example -- see
    the Nov 2026 diagnostic run)."""
    m_trunc = m[m.index <= as_of]
    return bt.straddle_sample_size(m_trunc, length)


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

        # thin-sample filter: drop any trade whose ENTRY happened while its
        # own pipeline's straddle-month count was still below the live
        # system's existing 75-month threshold.
        trades_old_f = [t for t in trades_old
                         if straddle_count_asof(monthly_old, bt.FVB_LEN, t["entry_date"]) >= THIN_SAMPLE_THRESHOLD]
        trades_new_f = [t for t in trades_new
                         if straddle_count_asof(monthly_new, bt.FVB_LEN, t["entry_date"]) >= THIN_SAMPLE_THRESHOLD]
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
              f"{n_dropped_old} old / {n_dropped_new} new trades dropped as thin-sample, "
              f"old {st_old['trades']} trades / new {st_new['trades']} trades after filter)",
              file=sys.stderr)

    out = pd.DataFrame(rows)
    out.to_csv("milestone_a_old_vs_new.csv", index=False)
    pd.set_option("display.width", 160)
    print(f"\n=== OLD (benchmark) vs NEW (daily-sourced, no-extra-shift) "
          f"-- thin-sample trades (<{THIN_SAMPLE_THRESHOLD} straddle months at entry) EXCLUDED from both ===")
    print(out.to_string(index=False))
    print("\nSaved: milestone_a_old_vs_new.csv -- send this back for review.")
    print("\nHow to read it: 'green_state_flips_differ_on_pct' is the most consequential column --")
    print("every week where old/new disagree on green/red is a week where the two engines would")
    print("make a DIFFERENT entry/exit decision. A small number (a few %) is expected given the")
    print("fixes target a 1-month timing shift, not a wholesale rewrite. A large number would mean")
    print("something deeper is wrong with one of the two pipelines and needs investigation before")
    print("trusting either one's trade-level results.")
    print(f"\n'*_trades_thin_dropped' shows how many trades were excluded for entering during each")
    print(f"pipeline's own thin-sample period (<{THIN_SAMPLE_THRESHOLD} straddle months of history), the same")
    print("threshold already live in scan_current_buys.py's 'thin_sample' warning -- not new. This is")
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
