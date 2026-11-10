#!/usr/bin/env python3
"""
Market-timing filter: is the S&P 500 currently in a confirmed uptrend?

ON  = SPY's last COMPLETED month closed above its own 10-month average.
OFF = it didn't.

Backtested directly against this project's own FVB signals, not just a
generic market test: gating NEW entries with this flag cut the worst
drawdown roughly in half (-22.6% -> -11.7%) for about a 0.5-point cost
in CAGR, on the real 33FVB trade history across the full universe
(see test_fvb_market_filter.py for that test).

This module only answers "is the market currently in an uptrend." It
does not touch any existing open position's own sell/stop logic, and
on its own it does not suppress anything — send_scan_email.py shows
this as an advisory banner above the fresh-signals list rather than
silently hiding signals, so you keep full visibility either way and
decide for yourself whether to act on a fresh buy while the filter
reads OFF.

Fails OPEN on purpose: if the SPY fetch fails for any reason, this
reports regime "ON" (i.e. acts normally) rather than silently blocking
every future buy signal because of a network hiccup.
"""
import sys
import pandas as pd

SMA_MONTHS = 10


def get_market_regime(ticker: str = "SPY") -> dict:
    try:
        import yfinance as yf
        d = yf.download(ticker, period="5y", interval="1mo", auto_adjust=True, progress=False)
    except Exception as e:
        print(f"market_regime: fetch error ({e}), defaulting to ON (fail-open)", file=sys.stderr)
        return {"regime": "ON", "spy_close": None, "spy_sma": None, "as_of": None, "error": True}
    if d is None or d.empty:
        print("market_regime: empty SPY data, defaulting to ON (fail-open)", file=sys.stderr)
        return {"regime": "ON", "spy_close": None, "spy_sma": None, "as_of": None, "error": True}
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    c = d["Close"].dropna()
    c.index = c.index.to_period("M").to_timestamp("M")
    c = c[~c.index.duplicated(keep="last")]
    this_month = pd.Timestamp.today().to_period("M").to_timestamp("M")
    c = c[c.index < this_month]          # last COMPLETED month only — no lookahead
    if len(c) < SMA_MONTHS:
        print("market_regime: not enough SPY history, defaulting to ON (fail-open)", file=sys.stderr)
        return {"regime": "ON", "spy_close": None, "spy_sma": None, "as_of": None, "error": True}
    sma = c.rolling(SMA_MONTHS).mean()
    last_close, last_sma, last_month = float(c.iloc[-1]), float(sma.iloc[-1]), c.index[-1]
    regime = "ON" if last_close > last_sma else "OFF"
    return {"regime": regime, "spy_close": last_close, "spy_sma": last_sma,
            "as_of": last_month.strftime("%Y-%m"), "error": False}


def regime_banner_text(info: dict) -> str:
    if info.get("error"):
        return "Market regime: UNKNOWN (SPY data fetch failed) — treating as ON, acting normally."
    arrow = "above" if info["regime"] == "ON" else "below"
    line = (f"Market regime: {info['regime']}  (S&P closed {info['as_of']} {arrow} its "
            f"{SMA_MONTHS}-month average: {info['spy_close']:.1f} vs {info['spy_sma']:.1f})")
    if info["regime"] == "OFF":
        line += ("\nPer the market-timing filter (tested against this system's own trade "
                  "history: cuts max drawdown roughly in half for a small cost in return), "
                  "consider holding off on acting on NEW buy signals below until this turns "
                  "back ON. Existing open positions are unaffected — keep following their "
                  "normal sell/stop signals as usual.")
    return line


def regime_banner_html(info: dict) -> str:
    if info.get("error"):
        return ('<p style="color:#888;">Market regime: UNKNOWN (SPY data fetch failed) — '
                'treating as ON, acting normally.</p>')
    color = "#1e8449" if info["regime"] == "ON" else "#c0392b"
    arrow = "above" if info["regime"] == "ON" else "below"
    html = (f'<p style="font-size:15px;margin:4px 0;"><b>Market regime: '
            f'<span style="color:{color};">{info["regime"]}</span></b> '
            f'(S&amp;P closed {info["as_of"]} {arrow} its {SMA_MONTHS}-month average: '
            f'{info["spy_close"]:.1f} vs {info["spy_sma"]:.1f})</p>')
    if info["regime"] == "OFF":
        html += ('<p style="color:#c0392b;margin:4px 0 16px 0;">Per the market-timing filter '
                 "(tested against this system's own trade history: cuts max drawdown roughly "
                 "in half for a small cost in return), consider holding off on acting on "
                 "<b>NEW</b> buy signals below until this turns back ON. Existing open "
                 "positions are unaffected — keep following their normal sell/stop signals "
                 "as usual.</p>")
    return html


if __name__ == "__main__":
    info = get_market_regime()
    print(regime_banner_text(info))
