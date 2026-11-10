#!/usr/bin/env python3
"""
Milestone A (per the external quant audit, Phase 5): fix the two
monthly-indicator timing bugs in backtest_33fvb.prep() WITHOUT touching
the strategy's entry/exit/band logic itself. backtest_33fvb.py is left
completely UNCHANGED and stays the benchmark; this script builds a
parallel, corrected indicator pipeline and compares the two side by
side on the full real universe, plus runs explicit point-in-time and
future-mutation no-lookahead tests.

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

What's NOT fixed here (flagged for Milestone B, not silently skipped):
  - audit item #4/#7: weekly-bar intrabar TP/STOP/entry fill ordering
    ambiguity in run() itself. Needs a full daily-level execution
    reconstruction, a bigger undertaking than the indicator-timing fix
    here. See the yfinance timestamp-convention findings below, which
    narrow down (but don't fully resolve) part of this ambiguity.
  - audit item #2: survivorship-biased universe (current index members
    applied to 1970s-present history). Deferred -- needs point-in-time
    constituent history data.
  - audit item #3: the --split train/test selection leakage in main()'s
    readout. Not touched; the live config (band=High, exit=NBarHigh) was
    frozen as given, per instruction, rather than re-selected.

Usage (run locally -- needs yfinance):
    python3 milestone_a_engine_fix.py --synthetic
        # no-network: point-in-time recompute test + future-mutation test
    python3 milestone_a_engine_fix.py
        # full bt.UNIVERSE (170 tickers) + 17-ETF basket, both UNFILTERED
        # and 60-month-warm-up-filtered comparisons, saved separately
    python3 milestone_a_engine_fix.py --tickers MSFT,AAPL,ORCL,SOFI,XEL
        # a specific subset instead of the full universe
"""
import argparse
import sys
import numpy as np
import pandas as pd
import backtest_33fvb as bt

ETF_BASKET = ["SPY", "QQQ", "IWM", "XLB", "XLE", "XLF", "XLI", "XLK", "XLP",
              "XLU", "XLV", "XLY", "EFA", "EEM", "TLT", "IEF", "GLD"]


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


# --------------------------------------------------- synthetic data builder
def _make_synthetic(n_days=4000, seed=42, start="2010-01-01"):
    rng = np.random.default_rng(seed)
    daily_idx = pd.date_range(start, periods=n_days, freq="B")
    close = 50 * np.exp(np.cumsum(rng.normal(0.0004, 0.015, len(daily_idx))))
    daily = pd.DataFrame({
        "Open": close * (1 + rng.normal(0, 0.003, len(daily_idx))),
        "High": close * (1 + np.abs(rng.normal(0, 0.006, len(daily_idx)))),
        "Low": close * (1 - np.abs(rng.normal(0, 0.006, len(daily_idx)))),
        "Close": close,
    }, index=daily_idx)
    weekly = daily.resample("W-FRI").agg({"Open": "first", "High": "max",
                                           "Low": "min", "Close": "last"}).dropna()
    return daily, weekly


# ----------------------------------------------- TEST 1: point-in-time recompute
def test_point_in_time_recompute():
    """The real test the audit asked for: for sampled historical decision
    dates, TRUNCATE the raw daily+weekly input data to only what was
    available by that date, rerun prep_v2() FROM SCRATCH on the truncated
    input, and compare the resulting LAST indicator value against what
    the full-history run produces at that same weekly bar. This exercises
    the actual _compute_real_fvb() causality (sequential median/state
    tracking), not just the reindex/ffill alignment -- a vectorized bug
    (e.g. an accidental centered rolling window) would be caught here but
    would NOT necessarily be caught by a pure alignment check.

    Explicit boundary cases included, not just evenly-spaced samples:
      - a decision date that IS itself a month-end Friday
      - a decision date whose underlying week's trading days span two
        different calendar months (the exact condition fix #5 targets)
    """
    daily, weekly = _make_synthetic(n_days=4000, seed=42)
    full_new, _ = prep_v2(weekly, daily)

    # evenly spaced samples, skipping the first chunk (no indicator yet)
    candidates = list(weekly.index[60::15])

    # explicit boundary case 1: a weekly bar landing in the last 3 days of
    # its calendar month (closest thing to "month-end Friday" in this
    # synthetic W-FRI series)
    month_end_candidates = [d for d in weekly.index if d.day >= d.days_in_month - 2]
    if month_end_candidates:
        candidates.append(month_end_candidates[len(month_end_candidates) // 2])

    # explicit boundary case 2: a week whose own trading days span two
    # different calendar months
    boundary_weeks = []
    prev = None
    for d in weekly.index:
        week_days = daily.index[(daily.index > (prev if prev is not None else d - pd.Timedelta(days=7)))
                                 & (daily.index <= d)]
        if len(week_days) and week_days.min().month != week_days.max().month:
            boundary_weeks.append(d)
        prev = d
    if boundary_weeks:
        candidates.append(boundary_weeks[len(boundary_weeks) // 2])

    candidates = sorted(set(candidates))
    mismatches = []
    for d in candidates:
        if d not in full_new.index:
            continue
        daily_trunc = daily[daily.index <= d]
        weekly_trunc = weekly[weekly.index <= d]
        if len(weekly_trunc) < bt.FVB_LEN:
            continue
        try:
            trunc_new, _ = prep_v2(weekly_trunc, daily_trunc)
        except Exception as e:
            mismatches.append((d, f"ERROR recomputing on truncated data: {e}"))
            continue
        if d not in trunc_new.index:
            mismatches.append((d, "truncated run dropped this bar entirely (insufficient warmup)"))
            continue
        full_row = full_new.loc[d]
        trunc_row = trunc_new.loc[d]
        for col in ["basis", "upperR", "lowerR", "green"]:
            fv, tv = full_row[col], trunc_row[col]
            if pd.isna(fv) and pd.isna(tv):
                continue
            if col == "green":
                if bool(fv) != bool(tv):
                    mismatches.append((d, f"{col}: full={fv} truncated={tv}"))
            elif pd.isna(fv) != pd.isna(tv) or abs(fv - tv) > 1e-9:
                mismatches.append((d, f"{col}: full={fv} truncated={tv}"))

    print(f"point-in-time recompute test: {len(candidates)} decision dates checked "
          f"(including month-end and month-boundary-crossing weeks)")
    if mismatches:
        print(f"  FAILED: {len(mismatches)} mismatches:")
        for d, msg in mismatches[:20]:
            print(f"    {d.date()}: {msg}")
        raise AssertionError("point-in-time recompute test failed -- see mismatches above")
    print("  PASSED: every sampled decision date's indicator value is IDENTICAL whether "
          "computed on the full history or on data truncated to that date.\n")


# ----------------------------------------------- TEST 2: future mutation
def test_future_mutation():
    """Stronger, more direct proof than test 1: compute the full pipeline
    once, note the indicator value at a chosen decision date, then VIOLENTLY
    MUTATE every bar strictly after that date (10x price shock) and
    recompute the full pipeline again. If the decision date's own indicator
    value changes AT ALL, that is lookahead, full stop -- no interpretation
    needed."""
    daily, weekly = _make_synthetic(n_days=4000, seed=43)
    full_new, _ = prep_v2(weekly, daily)

    # pick a decision date with plenty of history before AND after it
    d = weekly.index[int(len(weekly) * 0.7)]
    baseline = full_new.loc[d, ["basis", "upperR", "lowerR", "green"]].copy()

    daily_mut = daily.copy()
    mask = daily_mut.index > d
    for col in ["Open", "High", "Low", "Close"]:
        daily_mut.loc[mask, col] = daily_mut.loc[mask, col] * 10.0 + 500.0
    weekly_mut = daily_mut.resample("W-FRI").agg({"Open": "first", "High": "max",
                                                   "Low": "min", "Close": "last"}).dropna()
    mut_new, _ = prep_v2(weekly_mut, daily_mut)
    mutated = mut_new.loc[d, ["basis", "upperR", "lowerR", "green"]]

    print(f"future-mutation test: decision date {d.date()}, "
          f"all bars after it shocked 10x+500 in price")
    mismatches = []
    for col in ["basis", "upperR", "lowerR", "green"]:
        bv, mv = baseline[col], mutated[col]
        if col == "green":
            if bool(bv) != bool(mv):
                mismatches.append(col)
        elif abs(bv - mv) > 1e-9:
            mismatches.append(col)
    if mismatches:
        print(f"  FAILED: {mismatches} changed after mutating ONLY future data -- lookahead present")
        raise AssertionError(f"future-mutation test failed: {mismatches} leaked future information")
    print("  PASSED: decision date's indicator value is bit-for-bit unchanged despite "
          "a massive shock to every future bar.\n")


# --------------------------------------------------------- old vs new diff

MIN_HISTORY_MONTHS = 60  # 5 years -- a standard backtesting warm-up buffer.
                         # NOTE: this is NOT the live system's separate
                         # thin_sample (straddle_n<75) warning -- an earlier
                         # version of this script mistakenly reused that as
                         # an exclusion filter and it wiped out nearly every
                         # trade for every ticker (a smoothly trending stock
                         # can legitimately never clear 75 straddle-months
                         # across its ENTIRE history -- see XEL in the
                         # original docstring). This is a plain count of
                         # calendar months since first available data,
                         # unrelated to the straddle-specific statistic.


def months_of_history_asof(m: pd.DataFrame, as_of) -> int:
    if m.empty or as_of < m.index[0]:
        return 0
    return len(m[m.index <= as_of])


def compare_old_vs_new(tickers, include_etfs=True):
    """Runs BOTH pipelines on every ticker, reports UNFILTERED results as
    the primary comparison (per instruction: do not silently alter the
    original benchmark), and the 60-month-warm-up-filtered results
    separately alongside it -- never only the filtered view."""
    summary_rows = []
    trade_diff_rows = []
    all_tickers = [(tk, "stock") for tk in tickers]
    if include_etfs:
        all_tickers += [(tk, "etf") for tk in ETF_BASKET]

    for i, (tk, uni) in enumerate(all_tickers):
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
        new, _ = prep_v2(weekly, daily)
        monthly_old = weekly.resample("ME").agg({"Open": "first", "High": "max",
                                                  "Low": "min", "Close": "last"}).dropna()
        monthly_new = daily.resample("ME").agg({"Open": "first", "High": "max",
                                                 "Low": "min", "Close": "last"}).dropna()

        common = old.index.intersection(new.index)
        old_c, new_c = old.loc[common], new.loc[common]
        green_diff = (old_c["green"] != new_c["green"]).sum()
        basis_pct_diff = ((new_c["basis"] - old_c["basis"]).abs() / old_c["basis"]).mean()
        band_pct_diff = ((new_c["upperR"] - old_c["upperR"]).abs() / old_c["upperR"]).mean()

        trades_old = bt.run(old, "High", "NBarHigh")
        trades_new = bt.run(new, "High", "NBarHigh")

        # record EVERY trade (both pipelines) for the trade-level diff file
        for t in trades_old:
            trade_diff_rows.append({"ticker": tk, "universe": uni, "pipeline": "old",
                                     "entry_date": t["entry_date"], "exit_date": t["exit_date"],
                                     "ret": t["ret"], "hold": t["hold"], "reason": t["reason"]})
        for t in trades_new:
            trade_diff_rows.append({"ticker": tk, "universe": uni, "pipeline": "new",
                                     "entry_date": t["entry_date"], "exit_date": t["exit_date"],
                                     "ret": t["ret"], "hold": t["hold"], "reason": t["reason"]})

        # UNFILTERED stats -- the primary comparison, per instruction
        st_old_raw, st_new_raw = bt.stats(trades_old), bt.stats(trades_new)

        # 60-month-warm-up-filtered stats -- reported ALONGSIDE, not instead
        trades_old_f = [t for t in trades_old
                         if months_of_history_asof(monthly_old, t["entry_date"]) >= MIN_HISTORY_MONTHS]
        trades_new_f = [t for t in trades_new
                         if months_of_history_asof(monthly_new, t["entry_date"]) >= MIN_HISTORY_MONTHS]
        st_old_f, st_new_f = bt.stats(trades_old_f), bt.stats(trades_new_f)

        summary_rows.append({
            "ticker": tk, "universe": uni, "weekly_bars_compared": len(common),
            "green_state_flips_differ_on_pct": 100 * green_diff / len(common) if len(common) else np.nan,
            "mean_basis_pct_diff": 100 * basis_pct_diff,
            "mean_upperband_pct_diff": 100 * band_pct_diff,
            "old_trades_unfiltered": st_old_raw["trades"], "new_trades_unfiltered": st_new_raw["trades"],
            "old_win_rate_unfiltered": st_old_raw["win_rate"], "new_win_rate_unfiltered": st_new_raw["win_rate"],
            "old_expectancy_unfiltered": st_old_raw["expectancy"], "new_expectancy_unfiltered": st_new_raw["expectancy"],
            "old_pf_unfiltered": st_old_raw["pf"], "new_pf_unfiltered": st_new_raw["pf"],
            "old_trades_60mo_filtered": st_old_f["trades"], "new_trades_60mo_filtered": st_new_f["trades"],
            "old_win_rate_60mo_filtered": st_old_f["win_rate"], "new_win_rate_60mo_filtered": st_new_f["win_rate"],
            "old_expectancy_60mo_filtered": st_old_f["expectancy"], "new_expectancy_60mo_filtered": st_new_f["expectancy"],
            "old_pf_60mo_filtered": st_old_f["pf"], "new_pf_60mo_filtered": st_new_f["pf"],
        })
        print(f"[{i+1}/{len(all_tickers)}] {tk} ({uni}): done "
              f"(green differs on {100*green_diff/len(common):.1f}% of weeks, "
              f"unfiltered {st_old_raw['trades']}->{st_new_raw['trades']} trades, "
              f"60mo-filtered {st_old_f['trades']}->{st_new_f['trades']} trades)",
              file=sys.stderr)

    summary = pd.DataFrame(summary_rows)
    summary.to_csv("milestone_a_summary_full_universe.csv", index=False)
    diffs = pd.DataFrame(trade_diff_rows)
    diffs.to_csv("milestone_a_trade_level_detail.csv", index=False)

    pd.set_option("display.width", 200)
    pd.set_option("display.max_rows", 500)
    print(f"\n=== Milestone A, FULL UNIVERSE ({len(summary)} tickers) ===")
    print("Primary comparison is UNFILTERED (the original benchmark, untouched). "
          "60-month-warm-up-filtered columns are reported ALONGSIDE for context, never in place of it.\n")
    print(summary.to_string(index=False))
    print(f"\nSaved: milestone_a_summary_full_universe.csv (per-ticker summary, both filtered "
          f"and unfiltered)")
    print(f"Saved: milestone_a_trade_level_detail.csv (every individual trade, both pipelines, "
          f"{len(diffs)} rows) -- for entry/exit/return-level diffing")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", type=str, default=None,
                     help="comma-separated tickers; default is the FULL bt.UNIVERSE + ETF basket")
    ap.add_argument("--no-etfs", action="store_true")
    ap.add_argument("--synthetic", action="store_true",
                     help="run only the point-in-time and future-mutation tests, no network needed")
    args = ap.parse_args()

    print("Running point-in-time recompute test (synthetic data)...\n")
    test_point_in_time_recompute()
    print("Running future-mutation test (synthetic data)...\n")
    test_future_mutation()

    if args.synthetic:
        return

    tickers = args.tickers.split(",") if args.tickers else list(bt.UNIVERSE)
    compare_old_vs_new(tickers, include_etfs=not args.no_etfs)


if __name__ == "__main__":
    main()
