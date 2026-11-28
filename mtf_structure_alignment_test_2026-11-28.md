# Multi-timeframe (1H / Daily / Weekly) structure alignment — test, 2026-11-28

Forty's question: can we track when market structure (higher highs + higher lows =
real uptrend, as opposed to just the FVB band being green) agrees across 1H, daily
and weekly at once, and does that alignment give better entries?

We already had a validated DAILY-only version of this in production (shown on every
weekly scan email): confirmed swing pivots (10L/10R) on daily bars, BULLISH = HH+HL,
BEARISH = LH+LL. This test adds WEEKLY (same pivot logic, on the weekly bars already
fetched for every trade -- no new data) and HOURLY (new 60m data, but Yahoo only
gives ~730 days of it), then tags all 1,851 real historical FVB trades and checks
win rate / mean return by alignment.

## Results (1,851 historical trades, full universe/history)

**Daily alone** (reproduces the existing validated tag):
BULLISH 75.9% win / +19.0% mean (n=552) vs BEARISH 67.3% / +14.6% (n=544)

**Weekly alone** (new): less clean on its own —
BULLISH 73.1%/+16.5% (n=907), BEARISH 71.7%/+15.0% (n=244), MIXED worst at
68.3%/+13.0% (n=690). Weekly bearish isn't much worse than weekly bullish here.

**Daily + Weekly BOTH agree bullish** (n=297): **76.4% win / +17.7% mean**
vs everything that disagrees (n=1,459): 69.8% / +14.2% mean.
That's a real ~6.6pp win-rate edge on a decent sample -- genuinely better than the
daily tag alone, and free (no new data fetch needed).

Oddity: both agree BEARISH (n=85) did almost as well: 74.1%/+18.7%. Small-ish sample,
plausibly catching early reversals (FVB fires a buy right as a downtrend is about to
break), but not large enough to lean on either way.

**Hourly, only the 106/1,851 trades recent enough for Yahoo's 60m data (last ~2yr):**
BULLISH 92.9% win / +35.6% mean -- but **n=14**. BEARISH 73.3%/+18.2% (n=45),
MIXED 72.3%/+11.5% (n=47).

**Full 1H+Daily+Weekly agreement** (same 106-trade recent subset):
ALL 3 BULLISH n=3, ALL 3 BEARISH n=2 -- both "100% win" on samples this small is not
a result, it's noise. Everything else (n=101) sits at 74.3%/+16.8%.

## Verdict
- **Daily + Weekly agreement is a real, usable upgrade** to the existing daily-only
  tag -- same free data, ~6-7pp better win rate on a 297-trade sample. Worth adding
  to the scan as a second confidence tag alongside the existing daily one.
- **Hourly alignment is not usable yet, for a structural reason, not a bad-result
  reason**: our FVB trades hold for months, but free 60m data only covers the last
  ~2 years, so only 106 of 1,851 historical trades even have hourly data to check,
  and requiring all 3 timeframes to agree collapses that further to 3-5 trades.
  The 92.9%-win hourly-bullish number is exactly the kind of thin-sample trap we've
  flagged before (PPS Killer, Peter's band-bounce claim, Silver Bullet) -- interesting,
  not actionable.
- Entry-side only. **Exit-side (does structure degrading mid-trade give an earlier
  warning than the FVB band itself flipping) is not tested here** -- flagged as a
  follow-up, not attempted yet.

## Next steps (not done, pending direction)
1. Add a "Daily+Weekly structure" column to the live weekly scan email (cheap, real
   edge, no new data).
2. If still curious about hourly/intraday alignment, we'd need either a paid
   intraday data source with real history, or restrict the whole question to recent
   signals only (small sample, lower confidence) -- your call.
3. Separate study: does a structure flip (daily or weekly going from BULLISH/MIXED to
   BEARISH) *while already in a trade* give earlier warning than the FVB band itself
   turning red? Not tested yet.

Code: `test_mtf_structure.py`.
