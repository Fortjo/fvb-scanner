#!/usr/bin/env python3
"""
Current buy scan: which tickers are the strategy currently LONG on, right now?
================================================================================
Runs the exact same validated logic (High band + NBarHigh exit, real FVB
algorithm) through to the most recent available bar for every ticker in the
universe, and reports:
  - Position: LONG (currently in a trade) or FLAT (waiting for a touch)
  - If LONG: entry date, entry price, current price, open P&L, weeks held
  - FVB state (GREEN/RED) right now
  - If FLAT: how far current price sits from the entry band (how close to
    a fresh signal)

This mirrors Peter's own dashboard fields (Position, Open P&L) but scanned
across the whole universe at once, sorted to put fresh/recent LONG entries
first — those are the most actionable "just became a buy" signals, as
opposed to positions that have been open a long time already.

Requires backtest_33fvb.py in the same folder.

Run:
    python3 scan_current_buys.py                    # full universe
    python3 scan_current_buys.py --tickers MSFT,ORCL # specific names
    python3 scan_current_buys.py --fresh-weeks 8     # only flag LONGs opened
                                                       # in the last 8 weeks
                                                       # as "fresh" (default 12)
"""
import argparse
import sys
import numpy as np
import pandas as pd
import backtest_33fvb as bt

def scan_ticker(tk: str, fresh_weeks: int):
    try:
        raw = bt.fetch(tk)
    except Exception as e:
        print(f"{tk}: fetch error ({e})", file=sys.stderr)
        return None
    if raw is None:
        return None
    df = bt.prep(raw)
    if len(df) < bt.NBAR_LEN + 20:
        return None

    # historical stats for THIS ticker under the exact same strategy — used
    # to RATE a fresh entry, not just flag that one exists. A fresh signal
    # on a ticker that's won 90% of its past trades is a very different
    # thing from a fresh signal on one that's been a coin flip.
    hist_trades = bt.run(df, "High", "NBarHigh")
    hist_stats = bt.stats(hist_trades)

    frac = bt.BAND_FRACS["High"]
    o, hgh, lw, cl = (df[c].values for c in ["Open", "High", "Low", "Close"])
    basis, green = df["basis"].values, df["green"].values
    prev_hh = df["prev_hh"].values
    has_real = "upperR" in df.columns
    upperR = df["upperR"].values if has_real else None
    halfw = df["halfw"].values if not has_real else None
    idx = df.index

    in_pos, entry_px, entry_i = False, np.nan, -1
    for i in range(len(df)):
        band_px = (upperR[i] if has_real else basis[i] + frac * halfw[i])
        if in_pos and i > entry_i:
            exit_px, reason = None, None
            if not np.isnan(prev_hh[i]) and hgh[i] >= prev_hh[i]:
                exit_px, reason = max(o[i], prev_hh[i]), "TP"
            elif not green[i]:
                exit_px, reason = cl[i], "STOP"
            if exit_px is not None:
                in_pos = False
                continue
        if i > 0:
            prior_band_px = (upperR[i - 1] if has_real else basis[i - 1] + frac * halfw[i - 1])
        else:
            prior_band_px = np.nan
        if (not in_pos) and green[i] == True and not np.isnan(band_px) \
                and lw[i] <= band_px and i > 0 and not np.isnan(prior_band_px) \
                and cl[i - 1] > prior_band_px:                     # noqa: E712
            entry_px, entry_i, in_pos = min(o[i], band_px), i, True

    last_i = len(df) - 1
    result = {
        "ticker": tk,
        "fvb_state": "GREEN" if green[last_i] else "RED",
        "position": "LONG" if in_pos else "FLAT",
        "current_price": cl[last_i],
        "fresh": False,
        "below_stop_level": False,
        "hist_trades": hist_stats["trades"],
        "hist_win_rate": hist_stats["win_rate"],
        "hist_expectancy": hist_stats["expectancy"],
        "hist_pf": hist_stats["pf"],
    }
    # [RATING] transparent, not a black-box score — just a plain-language
    # read of this ticker's OWN track record under this exact strategy.
    # "Thin sample" matters as much as the win rate itself: a ticker with
    # 100% wins on 1 trade tells you almost nothing, same trap we flagged
    # repeatedly in the broad backtests.
    if hist_stats["trades"] < 2:
        rating = "Thin sample (n<2)"
    elif hist_stats["win_rate"] >= 80 and hist_stats["expectancy"] >= 10:
        rating = "Strong track record"
    elif hist_stats["win_rate"] >= 60 and hist_stats["expectancy"] >= 5:
        rating = "Moderate track record"
    elif hist_stats["win_rate"] < 50 or hist_stats["expectancy"] < 0:
        rating = "Weak track record"
    else:
        rating = "Mixed track record"
    result["rating"] = rating

    if in_pos:
        weeks_held = last_i - entry_i
        # [RISK/REWARD] distance from HERE to the TP trigger (the prevailing
        # 200-bar high) vs distance from HERE to the actual stop level (the
        # real lower band — the threshold that would flip the state to red).
        # This is the R:R for what's LEFT in the trade, not the R:R from the
        # original entry — exactly the "we're already 10% of the way toward
        # the exit, so the remaining edge is thinner" check.
        #
        # [BUG FIX] pct_to_risk can be NEGATIVE — price has already dropped
        # below the lower band on a closing basis, even if the sticky
        # Cross-state hasn't flipped to red yet (that only fires once the
        # bar's HIGH also stays below the band, not just the close — so
        # there's a real gap where price is already under the line but the
        # state hasn't caught up). The old code only computed a ratio when
        # pct_to_risk > 0, silently turning this into NaN instead of
        # surfacing what's actually an important warning sign.
        lowerB = df["lowerB"].values
        pct_to_tp = 100 * (prev_hh[last_i] / cl[last_i] - 1) if not np.isnan(prev_hh[last_i]) else np.nan
        pct_to_risk = 100 * (1 - lowerB[last_i] / cl[last_i]) if not np.isnan(lowerB[last_i]) else np.nan
        below_stop_level = (not np.isnan(pct_to_risk)) and pct_to_risk <= 0
        if not np.isnan(pct_to_tp) and not np.isnan(pct_to_risk) and pct_to_risk > 0:
            rr = pct_to_tp / pct_to_risk
        else:
            rr = np.nan
        result.update({
            "entry_date": idx[entry_i].strftime("%Y-%m-%d"),
            "entry_price": entry_px,
            "open_pl_pct": 100 * (cl[last_i] / entry_px - 1),
            "weeks_held": weeks_held,
            "fresh": weeks_held <= fresh_weeks,
            "pct_to_tp": pct_to_tp,
            "pct_to_risk": pct_to_risk,
            "risk_reward": rr,
            "below_stop_level": below_stop_level,
        })
    else:
        band_now = upperR[last_i] if has_real else basis[last_i] + frac * halfw[last_i]
        result["pct_from_band"] = 100 * (cl[last_i] / band_now - 1) if not np.isnan(band_now) else np.nan
    return result

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", type=str, default=None)
    ap.add_argument("--fresh-weeks", type=int, default=12)
    args = ap.parse_args()
    tickers = args.tickers.split(",") if args.tickers else bt.UNIVERSE

    rows = []
    for tk in tickers:
        r = scan_ticker(tk, args.fresh_weeks)
        if r is not None:
            rows.append(r)
        print(f"{tk}: scanned", file=sys.stderr)

    df = pd.DataFrame(rows)
    df.to_csv("current_buy_scan.csv", index=False)

    longs = df[df["position"] == "LONG"].copy()
    flats = df[df["position"] == "FLAT"].copy()

    print(f"\n=== CURRENTLY LONG ({len(longs)} of {len(df)} scanned) ===")
    if len(longs):
        longs = longs.sort_values("weeks_held")
        longs["fresh"] = longs["fresh"].astype(bool)
        fresh = longs[longs["fresh"]].sort_values("hist_expectancy", ascending=False)
        stale = longs[~longs["fresh"]]
        if len(fresh):
            print(f"\n-- FRESH entries (opened within last {args.fresh_weeks} weeks), sorted by track record --")
            print(fresh[["ticker", "entry_date", "open_pl_pct", "weeks_held",
                         "hist_trades", "hist_win_rate", "hist_expectancy", "rating",
                         "pct_to_tp", "pct_to_risk", "risk_reward"]].round(1).to_string(index=False))
            print("\n  pct_to_tp/pct_to_risk are measured from CURRENT price, not the original entry —")
            print("  a low risk_reward here means most of the move toward the exit has already happened,")
            print("  even if the trade is still fresh by time held.")
            below = fresh[fresh["below_stop_level"] == True]           # noqa: E712
            if len(below):
                print(f"\n  ** WARNING: {len(below)} of these are already trading BELOW the stop level on a")
                print(f"  closing basis, even though the sticky state hasn't flipped to RED yet (that only")
                print(f"  fires once the bar's HIGH also stays under the band, not just the close):")
                print("  " + ", ".join(below["ticker"].tolist()))
        if len(stale):
            print(f"\n-- Already-open positions (older than {args.fresh_weeks} weeks) --")
            print(stale[["ticker", "entry_date", "entry_price", "current_price",
                         "open_pl_pct", "weeks_held", "fvb_state",
                         "pct_to_tp", "pct_to_risk", "risk_reward"]].round(2).to_string(index=False))
    else:
        print("(none currently long)")

    print(f"\n=== CLOSEST TO A FRESH SIGNAL (currently FLAT, sorted by distance to band) ===")
    if len(flats):
        near = flats.sort_values("pct_from_band", key=abs).head(15)
        print(near[["ticker", "current_price", "pct_from_band", "fvb_state"]].round(2).to_string(index=False))

    print("\nSaved: current_buy_scan.csv")

if __name__ == "__main__":
    main()
