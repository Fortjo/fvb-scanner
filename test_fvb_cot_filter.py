#!/usr/bin/env python3
"""
COT (Commitments of Traders) positioning at entry vs FVB outcomes (chat, 2026-11-25).

The bigger-timeframe cousin of "no charts, just positioning": every week the
CFTC publishes who holds S&P 500 futures -- large speculators ("non-commercial",
hedge funds/CTAs) vs commercials (dealers/hedgers). Classic reading
(Larry Williams' COT index): extremes are contrarian -- speculators crowded
long = fragile, commercials heavily net long = bullish.

Data: CFTC public reporting API (Legacy, futures only), E-mini S&P 500
(contract code 13874A, ~1997 onward). Positions are as of Tuesday but
published Friday, so each report is only treated as known from the
following Saturday (no look-ahead).

COT index = where this week's NET position sits within its own last 156 weeks
(3 years) of range, 0 = most bearish seen, 100 = most bullish seen.

Same two-layer standard as every other filter (see test_fvb_vix_filter.py):
  1) trade level: FVB outcome by COT-index bucket at entry
  2) portfolio level, two rules fixed in advance (not tuned):
       R1 "crowded specs":      skip new entries when speculator COT index >= 90
       R2 "commercials agree":  take new entries only when commercial COT index >= 50
     vs no filter, judged against a calendar-circular-shift placebo, p = (+1)/(N+1).
Only trades entered after the COT index exists are used in every version.
Read-only research.
"""
from __future__ import annotations
import json
import sys
import urllib.request
import numpy as np
import pandas as pd
from portfolio_sim import build_portfolio, metrics
from test_fvb_vix_filter import collect_trades

API = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
CODE = "13874A"
LOOKBACK = 156
SLOTS = 15
CASH_R_WEEKLY = 0.0008
N_PLACEBO = 300
SEED = 42
MIN_SHIFT_WEEKS = 52


def fetch_cot() -> pd.DataFrame:
    url = (f"{API}?$where=cftc_contract_market_code='{CODE}'"
           f"&$order=report_date_as_yyyy_mm_dd&$limit=50000")
    req = urllib.request.Request(url.replace(" ", "%20"), headers={"User-Agent": "fvb-scanner"})
    data = json.load(urllib.request.urlopen(req, timeout=60))
    df = pd.DataFrame(data)
    need = ["report_date_as_yyyy_mm_dd", "noncomm_positions_long_all", "noncomm_positions_short_all",
            "comm_positions_long_all", "comm_positions_short_all", "open_interest_all"]
    missing = [c for c in need if c not in df.columns]
    if missing:
        print("Missing fields:", missing)
        print("Available:", sorted(df.columns)[:80])
        sys.exit(1)
    df = df[need].copy()
    df["date"] = pd.to_datetime(df.pop("report_date_as_yyyy_mm_dd"))
    for c in need[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna().drop_duplicates("date").set_index("date").sort_index()
    df["spec_net"] = df.noncomm_positions_long_all - df.noncomm_positions_short_all
    df["comm_net"] = df.comm_positions_long_all - df.comm_positions_short_all
    for col, out in (("spec_net", "spec_idx"), ("comm_net", "comm_idx")):
        lo = df[col].rolling(LOOKBACK, min_periods=LOOKBACK).min()
        hi = df[col].rolling(LOOKBACK, min_periods=LOOKBACK).max()
        df[out] = 100 * (df[col] - lo) / (hi - lo).replace(0, np.nan)
    df.index = df.index + pd.Timedelta(days=4)   # Tuesday positions -> known from Saturday
    return df.dropna(subset=["spec_idx", "comm_idx"])


def bucket_report(df, col, label):
    print(f"\nTRADE LEVEL -- by {label} at entry")
    bins = [0, 25, 50, 75, 90, 100.001]
    names = ["0-25", "25-50", "50-75", "75-90", "90-100"]
    b = pd.cut(df[col], bins=bins, labels=names, include_lowest=True)
    for n in names:
        s = df[b == n]
        if len(s):
            print(f"  index {n:7s} trades {len(s):5d}   win {100*(s.ret>0).mean():5.1f}%   "
                  f"mean {100*s.ret.mean():+6.1f}%   median {100*s.ret.median():+6.1f}%   "
                  f"distinct entry months {s.ed.dt.to_period('M').nunique()}")


def main():
    cot = fetch_cot()
    print(f"COT {CODE}: {len(cot)} weekly reports with a full 3-year index, "
          f"{cot.index[0].date()} -> {cot.index[-1].date()}")
    last = cot.iloc[-1]
    print(f"Latest: speculator index {last.spec_idx:.0f}, commercial index {last.comm_idx:.0f}")

    raw = collect_trades()
    rows = []
    for tk, ed, xd, ret in raw:
        if ed < cot.index[0]:
            continue
        r = cot.asof(ed)
        rows.append((tk, ed, xd, ret, float(r.spec_idx), float(r.comm_idx)))
    df = pd.DataFrame(rows, columns=["tk", "ed", "xd", "ret", "spec", "comm"])
    print(f"{len(raw)} raw FVB trades, {len(df)} entered while the COT index exists")
    print(f"  all: win {100*(df.ret>0).mean():.1f}%  mean {100*df.ret.mean():+.1f}%")
    bucket_report(df, "spec", "SPECULATOR COT index (high = specs crowded long)")
    bucket_report(df, "comm", "COMMERCIAL COT index (high = commercials most bullish)")

    trades = [(tk, ed, xd, ret, max((xd - ed).days / 7.0, 1 / 7)) for tk, ed, xd, ret, *_ in rows]
    eqA, takenA, _ = build_portfolio(trades, SLOTS, lambda tk, t: False, CASH_R_WEEKLY)
    mA = metrics(eqA)
    print(f"\nPORTFOLIO LEVEL ({SLOTS} slots, repaired engine)")
    print(f"  A) no filter:  CAGR {100*mA['cagr']:+.1f}%  max DD {100*mA['dd']:+.1f}%  "
          f"Sharpe {mA['sharpe']:.2f}  entries {takenA}")

    rules = {
        "R1 skip new entries when speculator index >= 90": cot.spec_idx >= 90,
        "R2 enter only when commercial index >= 50": cot.comm_idx < 50,
    }
    rng = np.random.default_rng(SEED)
    for name, skip in rules.items():
        skip = skip.astype(bool)

        def mk(series):
            return lambda tk, t: bool(series.asof(t)) if t >= series.index[0] else False

        eqB, takenB, skippedB = build_portfolio(trades, SLOTS, mk(skip), CASH_R_WEEKLY)
        mB = metrics(eqB)
        d_dd, d_sh, d_cagr = mB["dd"] - mA["dd"], mB["sharpe"] - mA["sharpe"], mB["cagr"] - mA["cagr"]
        p_dd, p_sh, p_cg = [], [], []
        n = len(skip)
        for _ in range(N_PLACEBO):
            k = int(rng.integers(MIN_SHIFT_WEEKS, n - MIN_SHIFT_WEEKS))
            sh = pd.Series(np.roll(skip.values, k), index=skip.index)
            mC = metrics(build_portfolio(trades, SLOTS, mk(sh), CASH_R_WEEKLY)[0])
            p_dd.append(mC["dd"] - mA["dd"]); p_sh.append(mC["sharpe"] - mA["sharpe"])
            p_cg.append(mC["cagr"] - mA["cagr"])
        pv = lambda arr, real: (np.sum(np.array(arr) >= real) + 1) / (N_PLACEBO + 1)
        print(f"\n  B) {name}  (rule ON {100*skip.mean():.0f}% of weeks)")
        print(f"     CAGR {100*mB['cagr']:+.1f}% ({100*d_cagr:+.2f}pts)  max DD {100*mB['dd']:+.1f}% "
              f"({100*d_dd:+.1f}pts)  Sharpe {mB['sharpe']:.2f} ({d_sh:+.2f})  "
              f"taken {takenB} skipped {skippedB}")
        print(f"     placebo ({N_PLACEBO} circular shifts): CAGR p={pv(p_cg, d_cagr):.3f}  "
              f"DD p={pv(p_dd, d_dd):.3f}  Sharpe p={pv(p_sh, d_sh):.3f}")


if __name__ == "__main__":
    main()
