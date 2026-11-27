#!/usr/bin/env python3
"""
Daily options-positioning log (started 2026-11-25).

Free historical options open-interest data doesn't exist, so the only way to
ever backtest gamma walls / dealer-gamma regime on OUR timeframe is to start
recording them ourselves. This runs once per US trading day (during market
hours -- Yahoo blanks open interest outside them) and appends one row per
ticker to gamma_log.csv:

  date, ticker, spot, vix, net_gex_bn, regime, call_wall, put_wall,
  call_oi_wall, put_oi_wall, gamma_flip, n_contracts

Method (naive dealer-gamma estimate, same idea as SpotGamma-style tools):
dealers assumed long calls / short puts from customer flow;
  GEX per strike = +/- gamma * OI * 100 * S^2 * 0.01  ($ gamma per 1% move)
Black-Scholes gamma from each contract's own IV (median-IV fallback for junk
values). Expirations within the next 45 days.
  call_wall  = strike with the largest call GEX
  put_wall   = strike with the largest (most negative) put GEX
  *_oi_wall  = same, by raw open interest
  gamma_flip = strike nearest spot where cumulative net GEX changes sign

Purely additive research log: reads nothing from and writes nothing to the
live scanner, emails or position files. If data is missing (holiday, Yahoo
outage) it logs nothing for that ticker and exits cleanly.
"""
from __future__ import annotations
import math
import os
import sys
from datetime import datetime, timezone
import pandas as pd
import yfinance as yf

TICKERS = ["SPY", "QQQ"]
MAX_DAYS = 45
R = 0.04
LOG = "gamma_log.csv"


def bs_gamma(S, K, T, iv):
    if T <= 0 or iv <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (math.log(S / K) + (R + 0.5 * iv * iv) * T) / (iv * math.sqrt(T))
    return math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) / (S * iv * math.sqrt(T))


def compute(ticker: str, now: datetime) -> dict | None:
    tk = yf.Ticker(ticker)
    hist = tk.history(period="5d")
    closes = hist["Close"].dropna()
    spot = float(closes.iloc[-1]) if not closes.empty else float("nan")
    # The calendar date this script happens to run on is NOT reliable: GitHub Actions
    # schedules fire late (sometimes hours late), and on US market holidays or before
    # the open there's no new session at all, so Yahoo just hands back the last real
    # trading day's close. Date every row by that REAL session, not by `now`, so a
    # late/holiday run naturally collapses into the session it actually reflects
    # instead of relabeling stale data as a brand-new day.
    trade_date = closes.index[-1].strftime("%Y-%m-%d") if not closes.empty else None
    if not (spot > 0):
        # fast_info is a different Yahoo endpoint -- try it before giving up
        try:
            spot = float(tk.fast_info["last_price"])
        except Exception:
            pass
    if not (spot > 0):
        print(f"{ticker}: no valid spot price (history tail: "
              f"{hist['Close'].tail(3).to_dict() if not hist.empty else 'EMPTY'}) -- skipped")
        return None
    if trade_date is None:
        print(f"{ticker}: no price history at all -- skipped")
        return None
    rows = []
    for exp in tk.options:
        exp_dt = datetime.strptime(exp, "%Y-%m-%d").replace(hour=21, tzinfo=timezone.utc)
        days = (exp_dt - now).total_seconds() / 86400
        if days < 0 or days > MAX_DAYS:
            continue
        T = max(days, 0.25) / 365
        ch = tk.option_chain(exp)
        for side, df, sign in (("C", ch.calls, 1), ("P", ch.puts, -1)):
            ivs = df["impliedVolatility"]
            sane = ivs[(ivs > 0.03) & (ivs < 3)]
            fb = float(sane.median()) if len(sane) else 0.20
            for _, r in df.iterrows():
                oi = r.get("openInterest")
                if pd.isna(oi) or oi <= 0:
                    continue
                iv = r.get("impliedVolatility")
                if pd.isna(iv) or iv < 0.03 or iv > 3:
                    iv = fb
                g = bs_gamma(spot, float(r["strike"]), T, float(iv))
                rows.append({"side": side, "strike": float(r["strike"]), "oi": int(oi),
                             "gex": sign * g * oi * 100 * spot * spot * 0.01})
    df = pd.DataFrame(rows)
    if df.empty or (df.side == "C").sum() < 20 or (df.side == "P").sum() < 20:
        print(f"{ticker}: not enough open-interest data right now ({len(df)} contracts) -- skipped")
        return None

    by = df.groupby(["side", "strike"]).agg(gex=("gex", "sum"), oi=("oi", "sum")).reset_index()
    calls, puts = by[by.side == "C"], by[by.side == "P"]
    net = df.gex.sum()
    cum = df.groupby("strike").gex.sum().sort_index().cumsum()
    flips = [cum.index[k] for k in range(1, len(cum)) if (cum.iloc[k - 1] < 0) != (cum.iloc[k] < 0)]
    flip = min(flips, key=lambda s: abs(s - spot)) if flips else None
    return {
        "ticker": ticker,
        "trade_date": trade_date,
        "spot": round(spot, 2),
        "net_gex_bn": round(net / 1e9, 3),
        "regime": "positive" if net > 0 else "negative",
        "call_wall": calls.loc[calls.gex.idxmax()].strike,
        "put_wall": puts.loc[puts.gex.idxmin()].strike,
        "call_oi_wall": calls.loc[calls.oi.idxmax()].strike,
        "put_oi_wall": puts.loc[puts.oi.idxmax()].strike,
        "gamma_flip": flip,
        "n_contracts": len(df),
    }


def main():
    now = datetime.now(timezone.utc)
    try:
        vix = round(float(yf.Ticker("^VIX").history(period="5d")["Close"].iloc[-1]), 2)
    except Exception:
        vix = None
    out = []
    for t in TICKERS:
        try:
            r = compute(t, now)
        except Exception as e:
            print(f"{t}: error {e!r} -- skipped")
            r = None
        if r:
            r = {"date": r.pop("trade_date"), "vix": vix, **r}
            out.append(r)
            print(f"{t}: session {r['date']}  spot {r['spot']}  call wall {r['call_wall']:g}  "
                  f"put wall {r['put_wall']:g}  flip {r['gamma_flip']}  "
                  f"net {r['net_gex_bn']:+.2f}B ({r['regime']})  VIX {vix}")
    if not out:
        print("Nothing logged today.")
        return
    new = pd.DataFrame(out)
    cols = ["date", "ticker", "spot", "vix", "net_gex_bn", "regime", "call_wall", "put_wall",
            "call_oi_wall", "put_oi_wall", "gamma_flip", "n_contracts"]
    new = new[cols]
    if os.path.exists(LOG):
        old = pd.read_csv(LOG)
        # dedupe by the REAL session date (not run time): if we already have this
        # ticker's reading for that trading day, this run is a re-fetch of stale
        # data (holiday, delayed scheduler, pre-open run) and adds nothing new
        key = set(zip(new.date, new.ticker))
        skipped = old[old.apply(lambda row: (row.date, row.ticker) in key, axis=1)]
        for _, row in skipped.iterrows():
            print(f"{row.ticker}: already have session {row.date} -- not re-logging as a new day")
        old = old[~old.apply(lambda row: (row.date, row.ticker) in key, axis=1)]
        new = pd.concat([old, new], ignore_index=True)
    new = new.sort_values(["date", "ticker"]).reset_index(drop=True)
    new.to_csv(LOG, index=False)
    print(f"Logged. {LOG} now has {len(new)} rows total.")


if __name__ == "__main__":
    sys.exit(main())
