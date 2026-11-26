# "PPS KILLER John Ehlers" test (2026-11-26)

Public script "RSI KILLER John Ehlers" by PpSignal01 (2018), protected source (code hidden).
Settings on Forty's chart: 14, 70, 30, div lookback 90, 21, 70, 30, 50, 34, 67.
Labels: Bottom, Top, Bear (Bull never fired in the sample).

Method: read the indicator's own plotted signals straight from TradingView for each symbol
(300 bars per symbol = free-plan history: ~6 years weekly, ~14 months daily), then compare the
price change N bars after each signal with the normal N-bar change for the same symbols.
Signals do not sit exactly on swing extremes, so they don't appear to be back-dated.

Weekly, 26 symbols (stocks, IWM, GLD, BTC):
- Bottom: after 13 / 26 weeks +1.8% / +8.1% vs normal +4.9% / +10.0% -> worse than random.
- Top:    after 26 weeks +9.5% (73% up) vs normal +10.0% -> no sell value.
- Bear:   after 26 weeks +9.4% (69% up) vs normal +10.0% -> no sell value.

Daily, 16 symbols:
- Bottom: after 20 days +2.7%, 74% up vs normal +1.6%, 54% -> mild short-term edge (n=53).
- Bear:   after 60 days -1.5% vs normal +5.7% -> looks useful as a caution flag (n=71),
  but only ~14 months of daily data, one market period, signals clustered in the same
  sell-offs -> not proven.

Verdict: no use as a buy signal for our monthly system; daily Bear label worth watching
only as a short-term caution flag. A proper multi-year test would need the code, which is hidden.
