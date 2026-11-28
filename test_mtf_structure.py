#!/usr/bin/env python3
"""
Multi-timeframe structure alignment test (chat, 2026-11-28).

Forty's question: can we track when market structure (higher highs + higher lows,
i.e. a real uptrend, not just the FVB band being green) agrees across 1H, daily AND
weekly at once -- and does that alignment actually give better entries (or earlier
warning on exits) for our FVB system?

We ALREADY have a validated daily-structure tag in production (backtest_33fvb.
daily_structure_tag, shown on every weekly scan email): confirmed swing pivots
(10 bars left/right) on DAILY bars, BULLISH = higher high + higher low, BEARISH =
lower high + lower low, MIXED otherwise. On 1,840 real FVB trades this already showed
a real gap: BULLISH entries 76.1% win / +18.7% exp vs BEARISH 67.1% / +14.5% exp.

This test asks: does adding WEEKLY structure (same pivot logic on the weekly bars we
already fetch for every trade -- no new data needed) and HOURLY structure (new 60m
data, but Yahoo only gives ~730 days of it, so this part only covers RECENT trades)
sharpen that further, i.e. is "full timeframe agreement" better than daily alone?

Method: run the real, validated 33FVB system (bt.run, High band, NBarHigh exit) over
the whole universe/history exactly as the live scanner does -- not a new strategy,
just tagging every HISTORICAL trade's entry with:
  daily_structure   (existing, from backtest_33fvb.daily_structure_tag)
  weekly_structure  (new: same pivot logic, on the weekly bars already in hand)
  hourly_structure  (new: 60m data, last ~730 days only -- most trades won't have it)
and comparing win rate / expectancy by how many timeframes agree BULLISH at entry.
Exit-timing (does structure degrading mid-trade give an earlier warning than the
FVB band itself) is flagged as a possible follow-up, not attempted here -- scoping
this to the entry-filter question first, the same way the original daily-structure
tag was validated before anything else was tried.
Read-only research. Does not change the live scanner or indicator.
"""
from __future__ import annotations
import sys
import numpy as np
import pandas as pd
import backtest_33fvb as bt

HOURLY_PIV_L = HOURLY_PIV_R = 10   # ~10 hours each side; exploratory, not tuned


def weekly_structure_tag(weekly_df: pd.DataFrame, asof_i: int, piv_l=5, piv_r=5) -> str:
    """Same logic as bt.daily_structure_tag, but on the WEEKLY bars we already have
    (no extra fetch) -- reads only pivots confirmed at or before asof_i (no lookahead:
    mirrors how the entry itself only ever sees pivots confirmed by that week)."""
    h = weekly_df["High"].values[:asof_i + 1]
    l = weekly_df["Low"].values[:asof_i + 1]
    if len(h) < piv_l + piv_r + 2:
        return "INSUFFICIENT"
    conf_h = bt._confirmed_pivots(h, piv_l, piv_r, "high")
    conf_l = bt._confirmed_pivots(l, piv_l, piv_r, "low")
    hi = np.where(~np.isnan(conf_h))[0]
    lo = np.where(~np.isnan(conf_l))[0]
    if len(hi) < 2 or len(lo) < 2:
        return "INSUFFICIENT"
    lh, ph = conf_h[hi[-1]], conf_h[hi[-2]]
    ll, pl = conf_l[lo[-1]], conf_l[lo[-2]]
    if lh > ph and ll > pl:
        return "BULLISH"
    if lh < ph and ll < pl:
        return "BEARISH"
    return "MIXED"


def fetch_hourly(ticker: str):
    import yfinance as yf
    try:
        df = yf.download(ticker, period="730d", interval="60m", auto_adjust=True, progress=False)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df[["Open", "High", "Low", "Close"]].dropna()


def hourly_structure_tag(hourly: pd.DataFrame, asof_ts, piv_l=HOURLY_PIV_L, piv_r=HOURLY_PIV_R) -> str:
    if hourly is None:
        return "NO_DATA"
    # yfinance's 60m index is tz-aware (America/New_York); entry dates from the
    # weekly fetch are tz-naive. Normalize both to naive before comparing.
    idx = hourly.index.tz_localize(None) if hourly.index.tz is not None else hourly.index
    asof = pd.Timestamp(asof_ts)
    asof = asof.tz_localize(None) if asof.tzinfo is not None else asof
    h = hourly.loc[idx <= asof]
    if len(h) < piv_l + piv_r + 2:
        return "INSUFFICIENT"
    conf_h = bt._confirmed_pivots(h["High"].values, piv_l, piv_r, "high")
    conf_l = bt._confirmed_pivots(h["Low"].values, piv_l, piv_r, "low")
    hi = np.where(~np.isnan(conf_h))[0]
    lo = np.where(~np.isnan(conf_l))[0]
    if len(hi) < 2 or len(lo) < 2:
        return "INSUFFICIENT"
    lh, ph = conf_h[hi[-1]], conf_h[hi[-2]]
    ll, pl = conf_l[lo[-1]], conf_l[lo[-2]]
    if lh > ph and ll > pl:
        return "BULLISH"
    if lh < ph and ll < pl:
        return "BEARISH"
    return "MIXED"


def bucket_stats(label, rows, key_fn):
    groups = {}
    for r in rows:
        k = key_fn(r)
        groups.setdefault(k, []).append(r["ret"])
    print(f"\n{label}")
    for k in sorted(groups, key=lambda x: -len(groups[x])):
        r = np.array(groups[k])
        w = (r > 0).mean() * 100
        exp = r.mean() * 100
        print(f"  {str(k):28s} n={len(r):5d}  win {w:5.1f}%  mean {exp:+6.2f}%")


def main():
    rows = []
    for tk in bt.UNIVERSE:
        try:
            w = bt.fetch(tk)
            if w is None or len(w) < bt.FVB_LEN * 5:
                continue
            daily = bt.fetch_daily(tk)
            p = bt.prep(w)
            trades = bt.run(p, band="High", exit_mode="NBarHigh")
            if not trades:
                continue
            idx = p.index
            for t in trades:
                ed = t["entry_date"]
                i = idx.get_loc(ed)
                d_tag = bt.daily_structure_tag(daily[daily.index <= ed]) if daily is not None else "NO_DATA"
                w_tag = weekly_structure_tag(p, i)
                rows.append({"tk": tk, "entry_date": ed, "ret": t["ret"],
                             "daily": d_tag, "weekly": w_tag})
        except Exception as e:
            print(f"{tk}: error {e}", file=sys.stderr)

    print(f"Total historical FVB trades tagged: {len(rows)} across universe\n")

    bucket_stats("BY DAILY STRUCTURE ONLY (reproducing the existing validated tag)",
                 rows, lambda r: r["daily"])
    bucket_stats("BY WEEKLY STRUCTURE ONLY (new)",
                 rows, lambda r: r["weekly"])

    def align(r):
        tags = (r["daily"], r["weekly"])
        if all(t == "BULLISH" for t in tags):
            return "BOTH BULLISH"
        if all(t == "BEARISH" for t in tags):
            return "BOTH BEARISH"
        if any(t == "INSUFFICIENT" or t == "NO_DATA" for t in tags):
            return "INSUFFICIENT"
        return "MIXED/DISAGREE"

    bucket_stats("BY DAILY+WEEKLY AGREEMENT (2-timeframe alignment)", rows, align)

    # ---- hourly, only for entries recent enough that 60m data covers them ----
    print("\nFetching hourly data for recent entries (last ~2 years only, Yahoo's 60m limit)...")
    recent = [r for r in rows if pd.Timestamp(r["entry_date"]).tz_localize(None) >=
              (pd.Timestamp.now() - pd.Timedelta(days=700))]
    print(f"{len(recent)} of {len(rows)} total trades fall in the hourly-data window.")
    hourly_cache = {}
    for r in recent:
        tk = r["tk"]
        if tk not in hourly_cache:
            hourly_cache[tk] = fetch_hourly(tk)
        r["hourly"] = hourly_structure_tag(hourly_cache[tk], pd.Timestamp(r["entry_date"]))

    if recent:
        bucket_stats("BY HOURLY STRUCTURE ONLY (recent trades only, n={})".format(len(recent)),
                     recent, lambda r: r["hourly"])

        def align3(r):
            tags = (r["daily"], r["weekly"], r["hourly"])
            if all(t == "BULLISH" for t in tags):
                return "ALL 3 BULLISH"
            if all(t in ("BEARISH",) for t in tags):
                return "ALL 3 BEARISH"
            if any(t in ("INSUFFICIENT", "NO_DATA") for t in tags):
                return "INSUFFICIENT"
            return "MIXED/DISAGREE"
        bucket_stats("BY 1H+DAILY+WEEKLY AGREEMENT (3-timeframe alignment, recent trades only)",
                     recent, align3)
    else:
        print("No recent trades to test hourly alignment on.")


if __name__ == "__main__":
    main()
