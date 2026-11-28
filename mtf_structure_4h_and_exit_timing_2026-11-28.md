# Follow-up: 4H structure + exit-timing test (2026-11-28)

Follow-up to `mtf_structure_alignment_test_2026-11-28.md`. Forty: "Hourly no, but
what about 4h? Let's go with sell as well."

## Part 1: 4H instead of 1H
Confirms the caveat flagged last time: switching to 4H bars does **not** fix the
sample-size problem, because Yahoo's intraday data is still capped at ~730 days
regardless of bar size (4H bars are just the same 60m feed bucketed coarser) — still
only 106 of 1,851 historical trades fall in the window.

Within that same small sample, 4H structure alone actually ran **backwards** from
intuition: BEARISH 4H read n=46, 78.3% win/+20.7% mean, vs BULLISH 4H n=18, 72.2%
win/+10.9% mean (worst of the three). Full daily+weekly+4H agreement collapsed to
n=4 (bearish) and n=1 (bullish) — not usable, same thin-sample trap as before.
**Verdict: 4H doesn't help either, for the same structural data-availability reason
as 1H. Would need paid/longer intraday history to test this properly.**

## Part 2: does a structure flip mid-trade warn earlier than the real FVB exit?
For all 1,851 historical trades (using the now-available `entry_px`/`exit_px` on
each trade — added as a small additive field to `backtest_33fvb.run()`, regression-
checked clean: SPY 8/8, SOFI 1/1, PLTR 0/0, aggregate 1,851 trades/71.2% win/+15.98%
expectancy/PF 3.58 vs baseline 1,847/71.1%/+15.98%/PF 3.56 — matches), walked forward
day-by-day (daily structure) and week-by-week (weekly structure, same timeframe as
the trades themselves) from entry to the real exit, looking for the first BEARISH
flip.

**DAILY structure flip found before the real exit: 1,659/1,851 trades (89.6%).**
Median lead time: **202 days** earlier than the real exit. If exited there instead:
mean **+1.50%** vs the real exit's mean **+15.27%** on those same trades — only
better on 484/1,659 (29%).

**WEEKLY structure flip found before the real exit: 1,269/1,851 (68.6%).**
Median lead: **27 weeks** earlier. If exited there instead: mean **+2.54%** vs real
**+15.33%** — only better on 394/1,269 (31%).

## Verdict: no, don't use structure flips as an exit signal — it's actively worse
Both daily and weekly structure "break" far too early and far too often relative to
how long these trades actually run. A structure flip isn't a rare, late warning sign
here -- it happens in the *majority* of trades, hundreds of days/dozens of weeks
before the real exit, simply because normal pullbacks inside a long uptrend produce
lower-high/lower-low readings on a shorter lookback all the time. Exiting on the
first flip would have cut the system's average per-trade return by roughly 85-90%
(from +15%+ down to +1.5-2.5%), because it chops off exactly the long trend-following
runs that are the system's actual edge -- it's only "better" on about 3 trades in 10
(the ones that reverse hard right after the flip), and worse on the other 7.

This is consistent with why the current exit rule (`basis_slope`: wait for the
monthly basis itself to turn down) is deliberately slow and patient rather than
reactive -- that patience is doing real work, not just leaving profit on the table.
**Not adding structure-flip exits to the live system.**

Code: `test_4h_structure_and_exits.py`. `backtest_33fvb.py`'s only change was the
additive `entry_px`/`exit_px` fields (no logic change, regression-checked).
