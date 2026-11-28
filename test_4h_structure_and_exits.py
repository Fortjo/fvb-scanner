#!/usr/bin/env python3
"""
Follow-up to mtf_structure_alignment_test (chat, 2026-11-28). Forty: "Hourly no, but
what about 4h? Let's go with sell as well."

PART 1 -- 4H structure instead of 1H.
IMPORTANT CAVEAT UP FRONT: this does NOT fix the sample-size problem from the hourly
test. Yahoo's intraday data is still capped at ~730 days regardless of what bar size
we resample it into -- 4H bars are built from the SAME 60m feed, just bucketed
coarser. So the number of historical trades that fall inside the available window is
the same ~106 of 1,851. What 4H *can* change is whether the structure read itself is
less noisy (fewer false pivots from hour-to-hour chop) -- that's the only thing being
tested here, not "more data."

PART 2 -- exit timing: does a structure flip to BEARISH *while already in a trade*
warn earlier than the FVB band itself flipping (the current STOP rule, basis_slope)?
For every real historical trade (entry_px/exit_px now stored on the trade dict -- see
backtest_33fvb.py's additive change), walk DAILY bars forward from entry and find the
first day the daily structure tag flips to BEARISH (no lookahead -- only pivots
confirmed by that day). If that day comes before the real exit, compare what exiting
there (at that day's close) would have made vs what the real FVB exit actually made.
Same question asked of WEEKLY structure (same timeframe as the trades themselves, so
likely more relevant than daily noise).
Read-only research. No changes to the live scanner/indicator/stop logic.
"""
from __future__ import annotations
import sys
import numpy as np
import pandas as pd
import backtest_33fvb as bt

PIV_4H_L = PIV_4H_R = 10


def fetch_4h(ticker: str):
    import yfinance as yf
    try:
        h = yf.download(ticker, period="730d", interval="60m", auto_adjust=True, progress=False)
    except Exception:
        return None
    if h is None or h.empty:
        return None
    if isinstance(h.columns, pd.MultiIndex):
        h.columns = h.columns.get_level_values(0)
    h = h[["Open", "High", "Low", "Close"]].dropna()
    four_h = h.resample("4h").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    return four_h


def structure_tag_asof(high, low, asof_i, piv_l, piv_r) -> str:
    h, l = high[:asof_i + 1], low[:asof_i + 1]
    if len(h) < piv_l + piv_r + 2:
        return "INSUFFICIENT"
    ch = bt._confirmed_pivots(h, piv_l, piv_r, "high")
    cl = bt._confirmed_pivots(l, piv_l, piv_r, "low")
    hi, lo = np.where(~np.isnan(ch))[0], np.where(~np.isnan(cl))[0]
    if len(hi) < 2 or len(lo) < 2:
        return "INSUFFICIENT"
    if ch[hi[-1]] > ch[hi[-2]] and cl[lo[-1]] > cl[lo[-2]]:
        return "BULLISH"
    if ch[hi[-1]] < ch[hi[-2]] and cl[lo[-1]] < cl[lo[-2]]:
        return "BEARISH"
    return "MIXED"


def bucket_stats(label, rows, key_fn):
    groups = {}
    for r in rows:
        groups.setdefault(key_fn(r), []).append(r["ret"])
    print(f"\n{label}")
    for k in sorted(groups, key=lambda x: -len(groups[x])):
        r = np.array(groups[k])
        print(f"  {str(k):28s} n={len(r):5d}  win {100*(r>0).mean():5.1f}%  mean {100*r.mean():+6.2f}%")


def part1_4h(all_trades):
    print("=" * 70)
    print("PART 1: 4H structure (same ~2yr Yahoo window as the hourly test)")
    print("=" * 70)
    recent = [r for r in all_trades if pd.Timestamp(r["entry_date"]).tz_localize(None) >=
              (pd.Timestamp.now() - pd.Timedelta(days=700))]
    print(f"{len(recent)} of {len(all_trades)} total trades fall in the 4H-data window.")
    cache = {}
    for r in recent:
        tk = r["tk"]
        if tk not in cache:
            cache[tk] = fetch_4h(tk)
        d = cache[tk]
        if d is None:
            r["tag_4h"] = "NO_DATA"
            continue
        idx = d.index.tz_localize(None) if d.index.tz is not None else d.index
        ed = pd.Timestamp(r["entry_date"])
        ed = ed.tz_localize(None) if ed.tzinfo is not None else ed
        asof = np.searchsorted(idx.values, np.datetime64(ed), side="right") - 1
        if asof < 0:
            r["tag_4h"] = "NO_DATA"
            continue
        r["tag_4h"] = structure_tag_asof(d["High"].values, d["Low"].values, asof, PIV_4H_L, PIV_4H_R)
    if recent:
        bucket_stats(f"BY 4H STRUCTURE ONLY (n={len(recent)})", recent, lambda r: r["tag_4h"])

        def align3(r):
            tags = (r["daily"], r["weekly"], r["tag_4h"])
            if all(t == "BULLISH" for t in tags):
                return "ALL 3 BULLISH (daily+weekly+4H)"
            if all(t == "BEARISH" for t in tags):
                return "ALL 3 BEARISH"
            if any(t in ("INSUFFICIENT", "NO_DATA") for t in tags):
                return "INSUFFICIENT"
            return "MIXED/DISAGREE"
        bucket_stats("BY DAILY+WEEKLY+4H AGREEMENT", recent, align3)
    else:
        print("No recent trades available.")


def _pivot_lookup_table(values_high, values_low, piv_l, piv_r):
    """Precompute confirmed pivots ONCE for a full series, then return a fast
    as-of lookup: given a bar position i, which two pivot highs/lows were
    confirmed at or before i. O(n) once per ticker instead of O(n) per day
    (the naive re-scan-from-scratch approach does not scale to walking every
    day of every trade across the whole universe)."""
    ch = bt._confirmed_pivots(values_high, piv_l, piv_r, "high")
    cl = bt._confirmed_pivots(values_low, piv_l, piv_r, "low")
    hi_idx = np.where(~np.isnan(ch))[0]
    lo_idx = np.where(~np.isnan(cl))[0]

    def tag_asof(i):
        hpos = np.searchsorted(hi_idx, i, side="right") - 1
        lpos = np.searchsorted(lo_idx, i, side="right") - 1
        if hpos < 1 or lpos < 1:
            return "INSUFFICIENT"
        lh, ph = ch[hi_idx[hpos]], ch[hi_idx[hpos - 1]]
        ll, pl = cl[lo_idx[lpos]], cl[lo_idx[lpos - 1]]
        if lh > ph and ll > pl:
            return "BULLISH"
        if lh < ph and ll < pl:
            return "BEARISH"
        return "MIXED"
    return tag_asof


def part2_exit_timing(all_trades):
    print("\n" + "=" * 70)
    print("PART 2: exit timing -- structure flip to BEARISH mid-trade vs real FVB exit")
    print("=" * 70)
    daily_cache = {}
    weekly_lookup_cache = {}
    n_daily_early, n_weekly_early = 0, 0
    daily_lead, weekly_lead = [], []
    daily_alt_ret, daily_real_ret = [], []
    weekly_alt_ret, weekly_real_ret = [], []
    for r in all_trades:
        tk = r["tk"]
        ed, xd = pd.Timestamp(r["entry_date"]), pd.Timestamp(r["exit_date"])
        entry_px = r["entry_px"]
        # ---- daily structure flip (lookup table built once per ticker) ----
        if tk not in daily_cache:
            try:
                d = bt.fetch_daily(tk)
            except Exception:
                d = None
            if d is not None:
                daily_cache[tk] = (d, _pivot_lookup_table(d["High"].values, d["Low"].values, 10, 10))
            else:
                daily_cache[tk] = (None, None)
        daily, daily_tag_asof = daily_cache[tk]
        if daily is not None:
            window_idx = daily.index[(daily.index > ed) & (daily.index <= xd)]
            flip_date = None
            for day in window_idx:
                pos = daily.index.get_loc(day)
                if daily_tag_asof(pos) == "BEARISH":
                    flip_date = day
                    break
            if flip_date is not None and flip_date < xd:
                n_daily_early += 1
                daily_lead.append((xd - flip_date).days)
                alt_px = daily.loc[flip_date, "Close"]
                daily_alt_ret.append(alt_px / entry_px - 1 - bt.RT_COST)
                daily_real_ret.append(r["ret"])
        # ---- weekly structure flip (same pivots as the FVB system, lookup table) ----
        wdf = r["_wdf"]
        if tk not in weekly_lookup_cache:
            weekly_lookup_cache[tk] = _pivot_lookup_table(wdf["High"].values, wdf["Low"].values, 5, 5)
        weekly_tag_asof = weekly_lookup_cache[tk]
        wi_entry, wi_exit = r["_i_entry"], r["_i_exit"]
        flip_i = None
        for wi in range(wi_entry + 1, wi_exit + 1):
            if weekly_tag_asof(wi) == "BEARISH":
                flip_i = wi
                break
        if flip_i is not None and flip_i < wi_exit:
            n_weekly_early += 1
            weekly_lead.append(wi_exit - flip_i)
            alt_px = wdf["Close"].values[flip_i]
            weekly_alt_ret.append(alt_px / entry_px - 1 - bt.RT_COST)
            weekly_real_ret.append(r["ret"])

    total = len(all_trades)
    print(f"\nTotal trades checked: {total}")
    print(f"\nDAILY structure flip to BEARISH, found before the real exit: "
          f"{n_daily_early} trades ({100*n_daily_early/total:.1f}%)")
    if daily_lead:
        print(f"  median lead time: {np.median(daily_lead):.0f} days earlier than the real exit")
        print(f"  if exited at the flip instead: mean {100*np.mean(daily_alt_ret):+.2f}% "
              f"vs real exit mean {100*np.mean(daily_real_ret):+.2f}% (same {len(daily_alt_ret)} trades)")
        better = sum(1 for a, b in zip(daily_alt_ret, daily_real_ret) if a > b)
        print(f"  early exit would have been BETTER on {better}/{len(daily_alt_ret)} of those trades")

    print(f"\nWEEKLY structure flip to BEARISH, found before the real exit: "
          f"{n_weekly_early} trades ({100*n_weekly_early/total:.1f}%)")
    if weekly_lead:
        print(f"  median lead time: {np.median(weekly_lead):.0f} weeks earlier than the real exit")
        print(f"  if exited at the flip instead: mean {100*np.mean(weekly_alt_ret):+.2f}% "
              f"vs real exit mean {100*np.mean(weekly_real_ret):+.2f}% (same {len(weekly_alt_ret)} trades)")
        better = sum(1 for a, b in zip(weekly_alt_ret, weekly_real_ret) if a > b)
        print(f"  early exit would have been BETTER on {better}/{len(weekly_alt_ret)} of those trades")


def main():
    all_trades = []
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
                i_entry = idx.get_loc(ed)
                i_exit = idx.get_loc(t["exit_date"])
                d_tag = bt.daily_structure_tag(daily[daily.index <= ed]) if daily is not None else "NO_DATA"
                h, l = p["High"].values[:i_entry + 1], p["Low"].values[:i_entry + 1]
                ch = bt._confirmed_pivots(h, 5, 5, "high")
                cl = bt._confirmed_pivots(l, 5, 5, "low")
                hi, lo = np.where(~np.isnan(ch))[0], np.where(~np.isnan(cl))[0]
                if len(hi) < 2 or len(lo) < 2:
                    w_tag = "INSUFFICIENT"
                elif ch[hi[-1]] > ch[hi[-2]] and cl[lo[-1]] > cl[lo[-2]]:
                    w_tag = "BULLISH"
                elif ch[hi[-1]] < ch[hi[-2]] and cl[lo[-1]] < cl[lo[-2]]:
                    w_tag = "BEARISH"
                else:
                    w_tag = "MIXED"
                all_trades.append({"tk": tk, "entry_date": ed, "exit_date": t["exit_date"],
                                    "ret": t["ret"], "entry_px": t["entry_px"], "exit_px": t["exit_px"],
                                    "daily": d_tag, "weekly": w_tag,
                                    "_wdf": p, "_i_entry": i_entry, "_i_exit": i_exit})
        except Exception as e:
            print(f"{tk}: error {e}", file=sys.stderr)

    print(f"Total historical FVB trades: {len(all_trades)}\n")
    part1_4h(all_trades)
    part2_exit_timing(all_trades)


if __name__ == "__main__":
    main()
