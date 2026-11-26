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

## Daily, as a complete system (2026-11-26 evening)
Buy at the close of a "Bottom" bar, sell at the close of the next "Top" or "Bear" bar; 0.3%
round-trip cost (FX fee + spread). 38 symbols (32 stocks/ETFs, 6 crypto), 300 daily bars each
(free-plan cap, ~14 months stocks / ~10 months crypto). Controls: buy-and-hold over the same
window, and 200 random sets of trades with the same count and the same holding lengths.

- All 38: 105 trades, 68% win, avg +4.4%, median hold 39 days, in the market 39% of the time.
  Per symbol +10.2% vs buy-hold +6.6% vs random same-length trades +5.8%.
  Beat buy-hold on 17/38, beat random on 26/38.
- Stocks/ETFs (32): 71% win, avg +5.2%; +13.3% vs buy-hold +14.8% (while invested only 35% of
  the time) vs random +7.0%; beat random on 24/32.
- Crypto (6): 50% win, avg +0.1%; -6.3% vs buy-hold -37.2% (it mostly kept you out of a crash)
  but no better than random entries (2/6).
- Selling only on "Top" (ignoring "Bear"): no better than random -> the Bear exit is what works.

Verdict: on daily stocks, Bottom->Top/Bear timing looks better than random and nearly matches
buy-hold with a third of the time in the market. Only ~14 months of data in one market period:
promising, not proven. Not related to the monthly FVB system.

## Timing FVB entries with the daily "Bottom" label (2026-11-26)
All 81 FVB entries since Oct 2025 (list_recent_fvb_entries.py), real PPS daily labels read from
TradingView. Rule: after the FVB signal week, wait up to 30 trading days for a daily Bottom and
buy at that close.
- 51/81 got a Bottom: entry on average only 0.7% cheaper (median 0.3%), cheaper on 27/51,
  median wait 8 days -> basically a coin flip.
- 30/81 got no Bottom within 30 days: those stocks had already run up; entering late cost ~5.7%.
- Always waiting = on average 1.7% WORSE entries than taking the FVB price.
- The signals that never gave a Bottom were the better trades (+12.1% to date, 24/30 up) vs
  +7.7% (33/51 up) for those that did -> skipping no-Bottom signals would drop the best ones.
Verdict: does not improve FVB entries; take the FVB entry as usual.
