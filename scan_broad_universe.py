#!/usr/bin/env python3
"""
Broad-universe high-conviction scan (chat, 2026-11-28).

Forty's question: our weekly scan only ever looks at a fixed ~170-name list, so a
stock outside it with the exact setup we trust most would never surface. Can we
scan a much wider universe (all US-listed common stocks above a liquidity floor)
and show ONLY names that hit every validated high-conviction trait at once, right
now?

Required traits (chosen together: "require ALL at once"):
  1. Fresh FVB buy signal (position LONG, entered within the freshness window)
  2. STRONG entry close (close_position >= bt.CLOSE_POSITION_MIN_THRESHOLD)
  3. Daily AND Weekly structure BOTH read BULLISH (the validated "agree" tag --
     76.4% win vs 69.8% when they disagree, see mtf_structure_alignment_test)
  4. Fundamentals PASS (Peter's checklist 2-4, via fundamentals_check.py)
  5. Risk/reward from current price >= RR_MIN
  6. NOT a thin band sample (straddle_n >= 75 -- enough real history behind the
     band itself, not just the historical trade count)

Pipeline (two stages, to keep this runnable in one GitHub Actions job):
  Stage A -- cheap liquidity screen. Pull the full list of US-listed common
    stocks (Nasdaq + NYSE/NYSE American/ARCA via nasdaqtrader.com's public symbol
    directories), batch-download 1 month of daily data (yfinance supports
    multi-ticker batched downloads, much faster than one call per ticker), and
    keep only names above a price and average-dollar-volume floor. This is NOT
    the validated FVB engine -- purely a liquidity/junk filter before the
    expensive full history pull.
  Stage B -- cheap weekly-only FVB check on every Stage-A survivor (position/
    freshness only, no daily fetch, no fundamentals -- keeps this fast across a
    few thousand names). Only the small number that are currently LONG and fresh
    go on to Stage C.
  Stage C -- full scan_current_buys.scan_ticker() (adds historical stats, daily
    structure, weekly structure, risk/reward) plus fundamentals_check.py, on the
    short Stage-B shortlist only. Final trait filter applied here.
Read-only research -- does not touch the live ~170-name scanner, positions, or
account state.
"""
from __future__ import annotations
import io
import sys
import numpy as np
import pandas as pd
import requests
import yfinance as yf
import backtest_33fvb as bt
import scan_current_buys as scb
import fundamentals_check as fc

PRICE_MIN = 5.0
DOLLAR_VOL_MIN = 10_000_000       # avg daily $ volume over the last month
RR_MIN = 1.5
FRESH_WEEKS = 12
BATCH_SIZE = 200


def get_us_common_stock_universe() -> list[str]:
    """Nasdaq's public symbol directories -- no key needed, used by data
    vendors everywhere as the standard free source of 'every US-listed
    ticker'. Filters out ETFs, test issues, and the obvious non-common-stock
    suffixes (warrants/units/rights/preferred)."""
    urls = {
        "nasdaqlisted": "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
        "otherlisted": "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
    }
    tickers = set()
    for name, url in urls.items():
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            df = pd.read_csv(io.StringIO(resp.text), sep="|")
            df = df[~df.iloc[:, 0].astype(str).str.contains("File Creation Time", na=False)]
        except Exception as e:
            print(f"{name}: failed to fetch ({e})", file=sys.stderr)
            continue
        sym_col = "Symbol" if "Symbol" in df.columns else "ACT Symbol"
        if "Test Issue" in df.columns:
            df = df[df["Test Issue"] == "N"]
        if "ETF" in df.columns:
            df = df[df["ETF"] == "N"]
        syms = df[sym_col].astype(str).str.strip()
        # drop obvious non-common-stock suffixes (warrants $W, units $U, rights
        # $R, preferred $P...) and anything with a separator character at all
        syms = syms[~syms.str.contains(r"[\.\$\^]", regex=True, na=False)]
        syms = syms[syms.str.match(r"^[A-Z]{1,5}$", na=False)]
        tickers.update(syms.tolist())
    return sorted(tickers)


def stage_a_liquidity_screen(tickers: list[str]) -> list[str]:
    survivors = []
    for i in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[i:i + BATCH_SIZE]
        try:
            data = yf.download(batch, period="1mo", interval="1d", group_by="ticker",
                                threads=True, progress=False, auto_adjust=True)
        except Exception as e:
            print(f"batch {i}: download failed ({e})", file=sys.stderr)
            continue
        for tk in batch:
            try:
                sub = data[tk] if len(batch) > 1 else data
                close = sub["Close"].dropna()
                vol = sub["Volume"].dropna()
                if close.empty or vol.empty:
                    continue
                last_px = float(close.iloc[-1])
                avg_dollar_vol = float((close * vol).tail(20).mean())
                if last_px >= PRICE_MIN and avg_dollar_vol >= DOLLAR_VOL_MIN:
                    survivors.append(tk)
            except Exception:
                continue
        print(f"  ...screened {min(i+BATCH_SIZE, len(tickers))}/{len(tickers)}, "
              f"{len(survivors)} liquid so far", file=sys.stderr)
    return survivors


def stage_b_quick_fvb_check(tk: str, fresh_weeks: int):
    """Trimmed copy of scan_current_buys.scan_ticker's position-tracking loop --
    position/freshness ONLY, no historical trade stats, no daily fetch. Kept
    deliberately cheap (one weekly fetch) so it's affordable across thousands
    of tickers; the full stats only get computed in Stage C on the shortlist."""
    try:
        raw = bt.fetch(tk)
    except Exception:
        return None
    if raw is None:
        return None
    df = bt.prep(raw)
    if len(df) < bt.NBAR_LEN + 20:
        return None
    frac = bt.BAND_FRACS["High"]
    o, hgh, lw, cl = (df[c].values for c in ["Open", "High", "Low", "Close"])
    basis, green = df["basis"].values, df["green"].values
    prev_hh = df["prev_hh"].values
    has_real = "upperR" in df.columns
    upperR = df["upperR"].values if has_real else None
    halfw = df["halfw"].values if not has_real else None
    in_pos, entry_i = False, -1
    for i in range(len(df)):
        band_px = (upperR[i] if has_real else basis[i] + frac * halfw[i])
        if in_pos and i > entry_i:
            if (not np.isnan(prev_hh[i]) and hgh[i] >= prev_hh[i]) or (not green[i]):
                in_pos = False
                continue
        if i > 0:
            prior_band_px = (upperR[i - 1] if has_real else basis[i - 1] + frac * halfw[i - 1])
        else:
            prior_band_px = np.nan
        if (not in_pos) and green[i] == True and not np.isnan(band_px) \
                and lw[i] <= band_px and i > 0 and not np.isnan(prior_band_px) \
                and cl[i - 1] > prior_band_px:                        # noqa: E712
            entry_i, in_pos = i, True
    last_i = len(df) - 1
    weeks_held = last_i - entry_i if in_pos else None
    return {"tk": tk, "position": "LONG" if in_pos else "FLAT",
            "fresh": in_pos and weeks_held is not None and weeks_held <= fresh_weeks}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="cap the raw universe to the first N tickers (testing only)")
    args = ap.parse_args()

    print("Fetching US common-stock universe (Nasdaq + NYSE/other)...")
    universe = get_us_common_stock_universe()
    print(f"Raw universe: {len(universe)} tickers")
    if args.limit:
        universe = universe[:args.limit]
        print(f"(--limit set: testing on first {len(universe)} only)")
    print()

    print(f"Stage A: liquidity screen (price>=${PRICE_MIN}, avg $vol>=${DOLLAR_VOL_MIN:,.0f}/day)...")
    liquid = stage_a_liquidity_screen(universe)
    print(f"\nStage A survivors: {len(liquid)} tickers\n")

    print("Stage B: quick FVB position/freshness check on all liquid survivors...")
    shortlist = []
    for n, tk in enumerate(liquid, 1):
        r = stage_b_quick_fvb_check(tk, FRESH_WEEKS)
        if r and r["position"] == "LONG" and r["fresh"]:
            shortlist.append(tk)
        if n % 250 == 0:
            print(f"  ...{n}/{len(liquid)} checked, {len(shortlist)} fresh so far", file=sys.stderr)
    print(f"\nStage B shortlist (fresh LONG signals): {len(shortlist)} tickers: {shortlist}\n")

    print("Stage C: full scan + fundamentals on the shortlist...")
    hits = []
    for tk in shortlist:
        try:
            r = scb.scan_ticker(tk, FRESH_WEEKS)
        except Exception as e:
            print(f"{tk}: scan error {e}", file=sys.stderr)
            continue
        if r is None:
            continue
        fund = fc.fundamentals_tag(tk)
        r["fund_rating"] = fund["rating"]
        r["fund_reason"] = fund["reason"]
        checks = {
            "fresh_long": r["position"] == "LONG" and r.get("fresh"),
            "strong_close": r.get("entry_strength") == "STRONG",
            "structure_confirmed": r.get("structure_confirmed") is True,
            "fundamentals_pass": fund["rating"] == "PASS",
            "good_rr": pd.notna(r.get("risk_reward")) and r["risk_reward"] >= RR_MIN,
            "not_thin": not r.get("thin_sample", True),
        }
        r["_checks"] = checks
        if all(checks.values()):
            hits.append(r)
        else:
            missed = [k for k, v in checks.items() if not v]
            print(f"  {tk}: close but missed {missed}", file=sys.stderr)

    print(f"\n{'='*70}\nFINAL: {len(hits)} tickers hit EVERY required trait right now\n{'='*70}")
    if not hits:
        print("None this run. See stderr log above for which trait each shortlist "
              "candidate missed.")
    for r in hits:
        print(f"\n{r['ticker']} — {r['rating']}")
        print(f"  Entered {r['entry_date']} ({r['weeks_held']:.0f} wks ago), open P&L {r['open_pl_pct']:+.1f}%")
        print(f"  History: {r['hist_trades']:.0f} trades, {r['hist_win_rate']:.1f}% win, "
              f"{r['hist_expectancy']:+.1f}% expectancy")
        print(f"  Risk/reward from here: {r['risk_reward']:.2f}")
        print(f"  Entry strength: STRONG close ({r['close_position']:.2f})")
        print(f"  Daily structure: {r['daily_structure']}  |  Weekly structure: {r['weekly_structure']}")
        print(f"  Fundamentals: {r['fund_rating']} — {r['fund_reason']}")


if __name__ == "__main__":
    main()
