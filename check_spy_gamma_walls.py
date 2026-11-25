#!/usr/bin/env python3
"""
One-off feasibility check (chat, 2026-11-25): can we reproduce the
"Call Wall" / "Put Wall" / gamma-regime inputs that IncomeSharks types into
his "Shark SPY Day Trader" TradingView indicator, using only FREE options
data (yfinance option chains: open interest + implied vol per strike)?

His Nov 24 chart inputs: Call Wall 730, Put Wall 715, regime "Negative Gamma".

Method (standard naive dealer-gamma estimate, same idea SpotGamma-style
tools use): dealers assumed long calls / short puts from customer flow ->
  call GEX per strike = +gamma * OI * 100 * S^2 * 0.01
  put  GEX per strike = -gamma * OI * 100 * S^2 * 0.01
(dollar gamma per 1% move). Gamma from Black-Scholes with each contract's
own implied vol. Call Wall = strike with the largest call GEX; Put Wall =
strike with the largest (most negative) put GEX. Net GEX sign = regime.
Print-only; not wired into anything live.
"""
from __future__ import annotations
import math
import sys
from datetime import datetime, timezone
import pandas as pd
import yfinance as yf

MAX_DAYS = 45
R = 0.04


def bs_gamma(S, K, T, iv):
    if T <= 0 or iv <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (math.log(S / K) + (R + 0.5 * iv * iv) * T) / (iv * math.sqrt(T))
    return math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) / (S * iv * math.sqrt(T))


def main(ticker="SPY"):
    tk = yf.Ticker(ticker)
    spot = float(tk.history(period="5d")["Close"].iloc[-1])
    now = datetime.now(timezone.utc)
    print(f"{ticker} spot ~ {spot:.2f}  ({now:%Y-%m-%d %H:%M} UTC)")

    rows = []
    used = []
    for exp in tk.options:
        exp_dt = datetime.strptime(exp, "%Y-%m-%d").replace(hour=21, tzinfo=timezone.utc)
        days = (exp_dt - now).total_seconds() / 86400
        if days < 0 or days > MAX_DAYS:
            continue
        T = max(days, 0.25) / 365
        ch = tk.option_chain(exp)
        used.append(exp)
        for side, df, sign in (("C", ch.calls, 1), ("P", ch.puts, -1)):
            for _, r in df.iterrows():
                oi = r.get("openInterest")
                iv = r.get("impliedVolatility")
                if pd.isna(oi) or pd.isna(iv) or oi <= 0 or iv < 0.01:
                    continue
                g = bs_gamma(spot, float(r["strike"]), T, float(iv))
                gex = sign * g * oi * 100 * spot * spot * 0.01
                rows.append({"exp": exp, "side": side, "strike": float(r["strike"]),
                             "oi": int(oi), "gex": gex})

    df = pd.DataFrame(rows)
    print(f"Expirations used ({len(used)}): {', '.join(used)}")
    if df.empty:
        print("No option data returned.")
        return

    by = df.groupby(["side", "strike"]).agg(gex=("gex", "sum"), oi=("oi", "sum")).reset_index()
    calls = by[by.side == "C"]
    puts = by[by.side == "P"]

    call_wall = calls.loc[calls.gex.idxmax()]
    put_wall = puts.loc[puts.gex.idxmin()]
    call_oi_wall = calls.loc[calls.oi.idxmax()]
    put_oi_wall = puts.loc[puts.oi.idxmax()]
    net = df.gex.sum()

    # gamma flip: strike where cumulative net GEX (low -> high strikes) crosses 0
    net_by_strike = df.groupby("strike").gex.sum().sort_index()
    cum = net_by_strike.cumsum()
    flip = None
    for k in range(1, len(cum)):
        if (cum.iloc[k - 1] < 0) != (cum.iloc[k] < 0):
            flip = cum.index[k]
            if abs(flip - spot) / spot < 0.08:
                break

    print(f"\nCall Wall (max call GEX):  {call_wall.strike:.0f}   (by raw OI: {call_oi_wall.strike:.0f})")
    print(f"Put Wall  (max put GEX):   {put_wall.strike:.0f}   (by raw OI: {put_oi_wall.strike:.0f})")
    print(f"Net GEX: {net/1e9:+.2f} B per 1% move -> {'POSITIVE' if net > 0 else 'NEGATIVE'} gamma")
    print(f"Gamma flip (approx): {flip}")

    print("\nTop 5 call strikes by GEX:")
    for _, r in calls.nlargest(5, "gex").iterrows():
        print(f"  {r.strike:.0f}  GEX {r.gex/1e9:+.2f}B  OI {r.oi:,}")
    print("Top 5 put strikes by GEX:")
    for _, r in puts.nsmallest(5, "gex").iterrows():
        print(f"  {r.strike:.0f}  GEX {r.gex/1e9:+.2f}B  OI {r.oi:,}")

    print("\nIncomeSharks Nov 24 chart inputs for comparison: Call Wall 730, Put Wall 715, Negative Gamma.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "SPY")
