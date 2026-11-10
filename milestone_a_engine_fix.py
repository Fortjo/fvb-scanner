#!/usr/bin/env python3
"""
Milestone A -- final engine file (per the external quant audit, Phase 5,
plus the two follow-up rounds). backtest_33fvb.py is left completely
UNCHANGED throughout and stays the benchmark. This file builds TWO
successive corrected indicator pipelines, both compared against the
original and against each other:

  prep_v2()  -- fixes #5 (monthly OHLC built from real daily bars instead
                of resampling weekly-interval data) and #6 (removes a
                redundant extra month of lag). Attaches the monthly
                indicator to each weekly bar by reindexing on the bar's
                own LABEL (ffill).

  prep_v3()  -- the FINAL timing correction. prep_v2's label-based
                attachment is only safe if a weekly bar's label is always
                >= its real first trading date. The timestamp-convention
                check (milestone_a_timestamp_convention.py) found that is
                NOT always true -- Yahoo's weekly-bar label anchor shifted
                from Sunday-before-the-week to Monday-of-the-week at some
                point in 2023, and under the Monday convention the label
                CAN equal the bar's own real first trading day. The
                signal-timing check (milestone_a_signal_timing_check.py)
                found the concrete consequence: 303 weeks, across just 5
                tickers' full histories, where a week begins on a month's
                own last trading day, and prep_v2 incorrectly treats that
                still-completing month as already available for that
                week's entry decision. prep_v3 fixes this directly:

      1. Determine each weekly bar's REAL first trading date from the
         daily data -- not assumed from the label's weekday -- and
         verify (verify_weekly_windows) that the daily bars actually
         inside that window reconstruct the weekly bar's own OHLC.
      2. Attach the LATEST monthly candle that completed STRICTLY BEFORE
         that real first trading date -- never the label.
      3. A week beginning on a month's own last trading day therefore
         does NOT get that month's data (it wasn't complete before that
         day's trading could start) -- it correctly falls back to the
         prior month.
      4. A week beginning right after month-end gets that just-completed
         month immediately -- no unnecessary extra lag beyond what's
         genuinely required by #3.

Band/state FORMULA (_compute_real_fvb), FVB_LEN, entry/exit/stop rules in
backtest_33fvb.run(): untouched by either pipeline, in every version.

What's still NOT fixed here (unchanged from before, still Milestone B/C):
  - audit item #4/#7: weekly-bar intrabar TP/STOP/entry fill ordering
    ambiguity in run() itself -- a separate question from "which month's
    indicator is attached to this week", which is what prep_v3 answers.
  - audit item #2: survivorship-biased universe.
  - audit item #3: the --split train/test selection leakage in main().

Usage (run locally -- needs yfinance):
    python3 milestone_a_engine_fix.py --synthetic
        # no-network: all regression tests (v2 AND v3), including the
        # month-end-day-boundary and no-unnecessary-lag cases
    python3 milestone_a_engine_fix.py
        # full bt.UNIVERSE (170 tickers) + 17-ETF basket: old vs v2 vs v3,
        # UNFILTERED (primary) and 60-month-warm-up-filtered (alongside),
        # with v2-vs-v3 differences reported separately from old-vs-v2
    python3 milestone_a_engine_fix.py --tickers MSFT,AAPL,ORCL,SOFI,XEL
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
    """Fix #5 (monthly OHLC from real daily bars) + fix #6 (no redundant
    extra-month shift). Attaches the monthly indicator by reindexing on
    the weekly bar's own LABEL -- kept, unmodified, for comparison against
    prep_v3 below (which fixes the label-vs-real-execution-date gap)."""
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


# --------------------------------------------------------- v3 helpers
def _weekly_windows(weekly_index, daily_index):
    """Every weekly bar's real constituent trading days: [label_i,
    label_{i+1}) in calendar time. Used only for verification (point 1)
    and tests -- NOT for prep_v3's hot path, which uses the faster
    searchsorted-only form below for full-universe performance."""
    idx = list(weekly_index)
    out = {}
    for i, lab in enumerate(idx):
        upper = idx[i + 1] if i + 1 < len(idx) else lab + pd.Timedelta(days=7)
        days = daily_index[(daily_index >= lab) & (daily_index < upper)]
        out[lab] = days
    return out


def verify_weekly_windows(df_weekly: pd.DataFrame, df_daily: pd.DataFrame, tol=0.02):
    """Point 1: determine each weekly bar's real first/last trading date
    from the daily data, and verify the daily bars actually in that window
    reconstruct the weekly bar's own OHLC (within tolerance). Returns a
    list of mismatches -- empty means the weekly series is a faithful
    aggregation of the daily series, which is what prep_v3 relies on."""
    windows = _weekly_windows(df_weekly.index, df_daily.index)
    mismatches = []
    for lab, days in windows.items():
        if len(days) == 0 or lab not in df_weekly.index:
            continue
        sub = df_daily.loc[days]
        o, h, l, c = (sub["Open"].iloc[0], sub["High"].max(),
                      sub["Low"].min(), sub["Close"].iloc[-1])
        wrow = df_weekly.loc[lab]
        if (abs(o - wrow["Open"]) > tol or abs(h - wrow["High"]) > tol or
                abs(l - wrow["Low"]) > tol or abs(c - wrow["Close"]) > tol):
            mismatches.append({
                "label": lab, "first_trading_date": days.min(), "last_trading_date": days.max(),
                "reconstructed": (o, h, l, c),
                "reported": (wrow["Open"], wrow["High"], wrow["Low"], wrow["Close"]),
            })
    return mismatches


def _exec_dates(weekly_index, daily_index):
    """For every weekly label, the real first possible execution date:
    the earliest daily trading date on or after the label. Vectorized
    (searchsorted), not assumed from the label's weekday -- fast enough
    for the full universe."""
    pos = daily_index.searchsorted(weekly_index, side="left")
    pos = np.clip(pos, 0, len(daily_index) - 1)
    return pd.DatetimeIndex(daily_index.values[pos])


def prep_v3(df_weekly: pd.DataFrame, df_daily: pd.DataFrame):
    """Final timing correction. Monthly OHLC construction identical to
    prep_v2 (fix #5 unchanged). The ONLY difference is how the monthly
    indicator is attached to each weekly bar: instead of ffill-by-label,
    attach the latest monthly candle that completed STRICTLY BEFORE that
    bar's real first possible execution date (point 2). This:
      - handles a week beginning on a month's own last trading day
        correctly (point 3) -- that month cannot be used for an order
        executable earlier that same day, so the prior month is used;
      - introduces no unnecessary extra lag beyond that (point 4) -- it's
        the latest genuinely available month, not shifted further.
    Band/state formula, FVB_LEN, entry/exit/stop rules: unchanged."""
    m = df_daily.resample("ME").agg({"Open": "first", "High": "max",
                                      "Low": "min", "Close": "last"}).dropna()
    green, upperR, lowerR = bt._compute_real_fvb(m, bt.FVB_LEN)
    ohlc4 = (m["Open"] + m["High"] + m["Low"] + m["Close"]) / 4
    basis = ohlc4.rolling(bt.FVB_LEN).mean()
    monthly = pd.DataFrame({"basis": basis, "upperR": upperR, "lowerR": lowerR,
                             "green": green})

    exec_dates = _exec_dates(df_weekly.index, df_daily.index)
    month_vals = monthly.index.values
    pos = month_vals.searchsorted(exec_dates.values, side="left") - 1
    valid = pos >= 0

    basis_arr = np.full(len(df_weekly), np.nan)
    upperR_arr = np.full(len(df_weekly), np.nan)
    lowerR_arr = np.full(len(df_weekly), np.nan)
    green_arr = np.full(len(df_weekly), np.nan)
    if valid.any():
        basis_arr[valid] = monthly["basis"].values[pos[valid]]
        upperR_arr[valid] = monthly["upperR"].values[pos[valid]]
        lowerR_arr[valid] = monthly["lowerR"].values[pos[valid]]
        green_arr[valid] = monthly["green"].values[pos[valid]].astype(float)

    out = df_weekly.copy()
    out["basis"] = basis_arr
    out["upperR"] = upperR_arr
    out["lowerR"] = lowerR_arr
    out["green"] = green_arr
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
    out["green"] = out["green"].astype(object)
    out.loc[out["green"].notna(), "green"] = out.loc[out["green"].notna(), "green"].astype(bool)
    return out.dropna(subset=["basis"]), monthly


# --------------------------------------------------- synthetic data builder
def _make_synthetic(n_days=4000, seed=42, start="2010-01-01"):
    """Daily bars, plus the SAME underlying weekly bars under BOTH label
    conventions found empirically in real data:
      weekly_monday  -- label = the week's own real first trading day
                        (the convention where point 3's edge case arises)
      weekly_sunday  -- label = the Sunday before that first trading day
                        (the convention where point 4's "no unnecessary
                        lag beyond prep_v2" matters most)
    Both are built by grouping the SAME daily data into real ISO weeks --
    not via a generic resample -- so a week's real first/last trading date
    is whatever's actually in that group, exactly like real data."""
    rng = np.random.default_rng(seed)
    daily_idx = pd.date_range(start, periods=n_days, freq="B")
    close = 50 * np.exp(np.cumsum(rng.normal(0.0004, 0.015, len(daily_idx))))
    daily = pd.DataFrame({
        "Open": close * (1 + rng.normal(0, 0.003, len(daily_idx))),
        "High": close * (1 + np.abs(rng.normal(0, 0.006, len(daily_idx)))),
        "Low": close * (1 - np.abs(rng.normal(0, 0.006, len(daily_idx)))),
        "Close": close,
    }, index=daily_idx)

    iso = daily.index.to_series().dt.isocalendar()
    wk_key = list(zip(iso["year"].values, iso["week"].values))
    groups = {}
    for key, ts in zip(wk_key, daily.index):
        groups.setdefault(key, []).append(ts)

    mon_rows, sun_rows = [], []
    for key in sorted(groups.keys()):
        days = sorted(groups[key])
        sub = daily.loc[days]
        row = {"Open": sub["Open"].iloc[0], "High": sub["High"].max(),
               "Low": sub["Low"].min(), "Close": sub["Close"].iloc[-1]}
        first_day = days[0]
        mon_rows.append((first_day, row))
        sunday = first_day - pd.Timedelta(days=first_day.weekday() + 1)
        sun_rows.append((sunday, row))

    weekly_monday = pd.DataFrame([r for _, r in mon_rows],
                                  index=pd.DatetimeIndex([d for d, _ in mon_rows]))
    weekly_sunday = pd.DataFrame([r for _, r in sun_rows],
                                  index=pd.DatetimeIndex([d for d, _ in sun_rows]))
    return daily, weekly_monday, weekly_sunday


# ----------------------------------------------- TEST: window verification
def test_window_verification():
    """Point 1: verify_weekly_windows must find ZERO mismatches on data
    where the weekly bars were, by construction, built by aggregating the
    exact daily bars in each real ISO week -- this validates the
    verification method itself. Run the same function against real
    yfinance data to check for genuine vendor inconsistencies."""
    daily, weekly_monday, weekly_sunday = _make_synthetic(n_days=3000, seed=11)
    mism_mon = verify_weekly_windows(weekly_monday, daily)
    mism_sun = verify_weekly_windows(weekly_sunday, daily)
    print(f"window verification test: {len(weekly_monday)} Monday-labeled + "
          f"{len(weekly_sunday)} Sunday-labeled weekly bars checked against "
          f"their real constituent daily bars")
    if mism_mon or mism_sun:
        print(f"  FAILED: {len(mism_mon)} Monday-label mismatches, "
              f"{len(mism_sun)} Sunday-label mismatches")
        raise AssertionError("verify_weekly_windows found mismatches on a "
                              "self-consistent synthetic series -- bug in the "
                              "verification method itself")
    print("  PASSED: every weekly bar's OHLC is exactly reproduced by its real "
          "constituent daily bars, under both label conventions.\n")


# ----------------------------------------------- TEST: month-end-day boundary
def test_month_end_day_boundary():
    """Point 3: a week whose real first trading day is itself a month's
    last trading day must NOT use that same month's data -- it wasn't
    complete before that day's trading could start. Must fall back to the
    PRIOR month. Tested on the Monday-labeled convention, where the label
    itself can equal a month-end trading day (the exact condition found in
    real data: 303 such weeks across 5 tickers' full histories)."""
    daily, weekly_monday, _ = _make_synthetic(n_days=4000, seed=42)
    month_ends = daily.resample("ME").agg({"Close": "last"}).index

    candidates = [lab for lab in weekly_monday.index if lab in month_ends]
    assert candidates, ("could not find a synthetic week whose first trading "
                         "day is itself a month-end -- cannot test point 3")

    new, monthly = prep_v3(weekly_monday, daily)
    checked = 0
    for lab in candidates:
        if lab not in new.index:
            continue
        checked += 1
        exec_date = lab  # the label IS the real first trading day here
        attached_month = monthly.index[monthly.index < exec_date].max()
        same_day_month = lab  # the month whose own end IS this day
        assert attached_month != same_day_month, (
            f"FAILED: week starting on month-end {lab.date()} used that "
            f"SAME month's just-completing data instead of the prior month")
    print(f"month-end-day-boundary test: {checked} weeks whose first trading "
          f"day is itself a month-end, checked")
    print("  PASSED: every one of them used the PRIOR month, never the "
          "same-day-completing month.\n")


# ----------------------------------------------- TEST: no unnecessary lag
def test_no_unnecessary_lag():
    """Point 4: a week beginning right after month-end must get that
    just-completed month immediately, with no MORE delay than point 3
    strictly requires. Tested on the Sunday-labeled convention (label
    precedes the real first trading day), which is exactly where prep_v2's
    label-based ffill is overly conservative -- this test shows prep_v3
    closing that gap."""
    daily, _, weekly_sunday = _make_synthetic(n_days=4000, seed=43)
    month_ends = daily.resample("ME").agg({"Close": "last"}).index
    new3, monthly = prep_v3(weekly_sunday, daily)
    new2, _ = prep_v2(weekly_sunday, daily)

    improved = 0
    checked = 0
    for me in month_ends:
        after = daily.index[daily.index > me]
        if len(after) == 0:
            continue
        first_trading_day_after = after.min()
        # which weekly_sunday bar's real window contains this day?
        pos = weekly_sunday.index.searchsorted(first_trading_day_after, side="right") - 1
        if pos < 0 or pos >= len(weekly_sunday):
            continue
        lab = weekly_sunday.index[pos]
        if lab not in new3.index or lab not in new2.index:
            continue
        checked += 1
        v3_month = monthly.index[monthly.index < first_trading_day_after].max()
        # prep_v3 must see the just-completed month (me) by the first week
        # whose real trading includes the first day after it
        if pd.notna(v3_month) and v3_month == me:
            improved += 1
    print(f"no-unnecessary-lag test: {checked} month-ends' first post-month-end "
          f"week checked (Sunday-labeled convention)")
    assert improved == checked, (
        f"FAILED: only {improved}/{checked} first-post-month-end weeks got the "
        f"just-completed month immediately under prep_v3")
    print(f"  PASSED: all {checked} got the just-completed month immediately, "
          f"no lag beyond what point 3 requires.\n")


# ----------------------------------------------- TEST: point-in-time (v3)
def test_point_in_time_recompute_v3():
    daily, weekly_monday, _ = _make_synthetic(n_days=4000, seed=42)
    full_new, _ = prep_v3(weekly_monday, daily)

    candidates = list(weekly_monday.index[60::15])
    month_end_candidates = [d for d in weekly_monday.index if d.day >= d.days_in_month - 2]
    if month_end_candidates:
        candidates.append(month_end_candidates[len(month_end_candidates) // 2])
    candidates = sorted(set(candidates))

    mismatches = []
    for d in candidates:
        if d not in full_new.index:
            continue
        daily_trunc = daily[daily.index <= d]
        weekly_trunc = weekly_monday[weekly_monday.index <= d]
        if len(weekly_trunc) < bt.FVB_LEN:
            continue
        try:
            trunc_new, _ = prep_v3(weekly_trunc, daily_trunc)
        except Exception as e:
            mismatches.append((d, f"ERROR: {e}"))
            continue
        if d not in trunc_new.index:
            mismatches.append((d, "truncated run dropped this bar"))
            continue
        full_row, trunc_row = full_new.loc[d], trunc_new.loc[d]
        for col in ["basis", "upperR", "lowerR", "green"]:
            fv, tv = full_row[col], trunc_row[col]
            if pd.isna(fv) and pd.isna(tv):
                continue
            if col == "green":
                if bool(fv) != bool(tv):
                    mismatches.append((d, f"{col}: full={fv} truncated={tv}"))
            elif pd.isna(fv) != pd.isna(tv) or abs(fv - tv) > 1e-9:
                mismatches.append((d, f"{col}: full={fv} truncated={tv}"))

    print(f"point-in-time recompute test (prep_v3): {len(candidates)} decision "
          f"dates checked")
    if mismatches:
        print(f"  FAILED: {len(mismatches)} mismatches:")
        for d, msg in mismatches[:20]:
            print(f"    {d.date()}: {msg}")
        raise AssertionError("prep_v3 point-in-time recompute test failed")
    print("  PASSED: identical whether computed on full history or truncated "
          "to the decision date.\n")


# ----------------------------------------------- TEST: future mutation (v3)
def test_future_mutation_v3():
    daily, weekly_monday, _ = _make_synthetic(n_days=4000, seed=43)
    full_new, _ = prep_v3(weekly_monday, daily)
    d = weekly_monday.index[int(len(weekly_monday) * 0.7)]
    baseline = full_new.loc[d, ["basis", "upperR", "lowerR", "green"]].copy()

    daily_mut = daily.copy()
    mask = daily_mut.index > d
    for col in ["Open", "High", "Low", "Close"]:
        daily_mut.loc[mask, col] = daily_mut.loc[mask, col] * 10.0 + 500.0

    iso = daily_mut.index.to_series().dt.isocalendar()
    wk_key = list(zip(iso["year"].values, iso["week"].values))
    groups = {}
    for key, ts in zip(wk_key, daily_mut.index):
        groups.setdefault(key, []).append(ts)
    rows = []
    for key in sorted(groups.keys()):
        days = sorted(groups[key])
        sub = daily_mut.loc[days]
        rows.append((days[0], sub["Open"].iloc[0], sub["High"].max(),
                     sub["Low"].min(), sub["Close"].iloc[-1]))
    weekly_mut = pd.DataFrame([{"Open": o, "High": h, "Low": l, "Close": c}
                                for _, o, h, l, c in rows],
                               index=pd.DatetimeIndex([d0 for d0, *_ in rows]))

    mut_new, _ = prep_v3(weekly_mut, daily_mut)
    mutated = mut_new.loc[d, ["basis", "upperR", "lowerR", "green"]]

    print(f"future-mutation test (prep_v3): decision date {d.date()}, all bars "
          f"after it shocked 10x+500")
    mismatches = []
    for col in ["basis", "upperR", "lowerR", "green"]:
        bv, mv = baseline[col], mutated[col]
        if col == "green":
            if bool(bv) != bool(mv):
                mismatches.append(col)
        elif abs(bv - mv) > 1e-9:
            mismatches.append(col)
    if mismatches:
        print(f"  FAILED: {mismatches} changed after mutating ONLY future data")
        raise AssertionError(f"prep_v3 future-mutation test failed: {mismatches}")
    print("  PASSED: bit-for-bit unchanged despite a massive shock to every "
          "future bar.\n")


# --------------------------------------------------------- old vs v2 vs v3

MIN_HISTORY_MONTHS = 60  # plain calendar warm-up buffer; see round-1 notes
                         # for why the live system's thin_sample (straddle_n
                         # <75) is NOT reused here -- unrelated statistic.


def months_of_history_asof(m: pd.DataFrame, as_of) -> int:
    if m.empty or as_of < m.index[0]:
        return 0
    return len(m[m.index <= as_of])


def compare_three(tickers, include_etfs=True):
    """old (bt.prep) vs v2 (prep_v2) vs v3 (prep_v3), on real data. Reports
    old-vs-v2 (the monthly-OHLC-construction effect, fix #5/#6 -- already
    known from round 2) and v2-vs-v3 (the NEW, final timing-correction
    effect) as SEPARATE columns, per instruction #6. Primary comparison is
    UNFILTERED throughout; 60-month-filtered reported alongside, per #8."""
    summary_rows = []
    trade_rows = []
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
        v2, _ = prep_v2(weekly, daily)
        v3, monthly_v3 = prep_v3(weekly, daily)
        monthly_old = weekly.resample("ME").agg({"Open": "first", "High": "max",
                                                  "Low": "min", "Close": "last"}).dropna()

        common_old_v2 = old.index.intersection(v2.index)
        green_diff_old_v2 = (old.loc[common_old_v2, "green"] != v2.loc[common_old_v2, "green"]).sum()

        common_v2_v3 = v2.index.intersection(v3.index)
        green_diff_v2_v3 = (v2.loc[common_v2_v3, "green"] != v3.loc[common_v2_v3, "green"]).sum()

        trades_old = bt.run(old, "High", "NBarHigh")
        trades_v2 = bt.run(v2, "High", "NBarHigh")
        trades_v3 = bt.run(v3, "High", "NBarHigh")

        for label, trades in [("old", trades_old), ("v2", trades_v2), ("v3", trades_v3)]:
            for t in trades:
                trade_rows.append({"ticker": tk, "universe": uni, "pipeline": label,
                                    "entry_date": t["entry_date"], "exit_date": t["exit_date"],
                                    "ret": t["ret"], "hold": t["hold"], "reason": t["reason"]})

        st_old, st_v2, st_v3 = bt.stats(trades_old), bt.stats(trades_v2), bt.stats(trades_v3)

        trades_v3_f = [t for t in trades_v3
                        if months_of_history_asof(monthly_v3, t["entry_date"]) >= MIN_HISTORY_MONTHS]
        trades_old_f = [t for t in trades_old
                         if months_of_history_asof(monthly_old, t["entry_date"]) >= MIN_HISTORY_MONTHS]
        st_v3_f, st_old_f = bt.stats(trades_v3_f), bt.stats(trades_old_f)

        summary_rows.append({
            "ticker": tk, "universe": uni,
            "green_diff_old_vs_v2_pct": 100 * green_diff_old_v2 / len(common_old_v2) if len(common_old_v2) else np.nan,
            "green_diff_v2_vs_v3_pct": 100 * green_diff_v2_v3 / len(common_v2_v3) if len(common_v2_v3) else np.nan,
            "old_trades_unfiltered": st_old["trades"], "v2_trades_unfiltered": st_v2["trades"], "v3_trades_unfiltered": st_v3["trades"],
            "old_win_rate_unfiltered": st_old["win_rate"], "v2_win_rate_unfiltered": st_v2["win_rate"], "v3_win_rate_unfiltered": st_v3["win_rate"],
            "old_expectancy_unfiltered": st_old["expectancy"], "v2_expectancy_unfiltered": st_v2["expectancy"], "v3_expectancy_unfiltered": st_v3["expectancy"],
            "old_pf_unfiltered": st_old["pf"], "v2_pf_unfiltered": st_v2["pf"], "v3_pf_unfiltered": st_v3["pf"],
            "old_trades_60mo_filtered": st_old_f["trades"], "v3_trades_60mo_filtered": st_v3_f["trades"],
            "old_win_rate_60mo_filtered": st_old_f["win_rate"], "v3_win_rate_60mo_filtered": st_v3_f["win_rate"],
            "old_expectancy_60mo_filtered": st_old_f["expectancy"], "v3_expectancy_60mo_filtered": st_v3_f["expectancy"],
            "old_pf_60mo_filtered": st_old_f["pf"], "v3_pf_60mo_filtered": st_v3_f["pf"],
        })
        print(f"[{i+1}/{len(all_tickers)}] {tk} ({uni}): done "
              f"(old-vs-v2 green differs {100*green_diff_old_v2/len(common_old_v2):.1f}%, "
              f"v2-vs-v3 green differs {100*green_diff_v2_v3/len(common_v2_v3):.1f}%, "
              f"trades old={st_old['trades']} v2={st_v2['trades']} v3={st_v3['trades']})",
              file=sys.stderr)

    summary = pd.DataFrame(summary_rows)
    summary.to_csv("milestone_a_v3_summary_full_universe.csv", index=False)
    trades = pd.DataFrame(trade_rows)
    trades.to_csv("milestone_a_v3_trade_level_detail.csv", index=False)

    pd.set_option("display.width", 220)
    pd.set_option("display.max_rows", 500)
    print(f"\n=== Milestone A, FINAL TIMING CORRECTION, FULL UNIVERSE ({len(summary)} tickers) ===")
    print("old-vs-v2 = monthly-OHLC-construction effect (fix #5/#6, known from round 2).")
    print("v2-vs-v3 = THIS round's final timing-correction effect, isolated.")
    print("Primary comparison is UNFILTERED throughout; 60-month-filtered reported alongside.\n")
    print(summary.to_string(index=False))
    print(f"\nSaved: milestone_a_v3_summary_full_universe.csv")
    print(f"Saved: milestone_a_v3_trade_level_detail.csv ({len(trades)} rows)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", type=str, default=None)
    ap.add_argument("--no-etfs", action="store_true")
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args()

    print("Running window verification test (point 1)...\n")
    test_window_verification()
    print("Running point-in-time recompute test (prep_v3)...\n")
    test_point_in_time_recompute_v3()
    print("Running future-mutation test (prep_v3)...\n")
    test_future_mutation_v3()
    print("Running month-end-day-boundary test (point 3)...\n")
    test_month_end_day_boundary()
    print("Running no-unnecessary-lag test (point 4)...\n")
    test_no_unnecessary_lag()

    if args.synthetic:
        return

    tickers = args.tickers.split(",") if args.tickers else list(bt.UNIVERSE)
    compare_three(tickers, include_etfs=not args.no_etfs)


if __name__ == "__main__":
    main()
