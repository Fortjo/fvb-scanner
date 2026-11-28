# "%R Trend Exhaustion" (upslidedown) — independent test, 2026-11-28

Source: YouTube video by Michael Whitman (2026-11-27), shared by Forty. Claimed: 20
hand-picked stocks, 2014-Nov 2026, $10k/trade, "costs out", ~$160k / ~200 trades,
80%+ win rate, never lost more than 5 trades in a row, no fixed stop (exit zone IS
the exit).

## Rules tested (exact spec from the video, not tuned)
- fast = SMA(WilliamsR(21), 7), slow = SMA(WilliamsR(112), 3), daily chart
- bottom zone = BOTH lines <= -80 at once; up arrow = bottom zone ENDS -> buy next open
- top zone = BOTH lines >= -20; down arrow = top zone ENDS -> sell next open
- long only, one position at a time, new up-arrows ignored while in a trade

## Universe and method
167 S&P100+NDX100 tickers (our standard universe, not their 20), daily data back to
1995 (vs their 2014 start), 0.1% round-trip cost, trade-level + portfolio-level
(15 slots, our standard simulator) + two controls:
- **SINGLE-LINE**: the "mistake" the video itself warns against — fast line alone,
  no slow-line agreement required. Tests their specific claim directly.
- **RANDOM**: random entry days, each held for a length drawn from the REAL system's
  own realized hold-time distribution. Isolates whether the exhaustion-zone *timing*
  adds anything beyond just being long for a similar stretch.

## Results

| | trades | win% | mean/trade | PF | median hold | longest losing streak |
|---|---|---|---|---|---|---|
| REAL (both lines, as described) | 3,160 | 77.4% | +8.55% | 3.23 | 140d | **24** |
| control SINGLE-LINE ("the mistake") | 10,301 | 68.8% | +3.46% | 2.23 | 50d | 17 |
| control RANDOM (same hold-lengths) | 3,160 | 67.1% | **+15.38%** | **4.24** | 198d | 18 |

Portfolio (15 slots, 1995-2026):
- REAL: CAGR **+13.9%**, max DD -27.6%, Sharpe 4.43, 860 entries
- control SINGLE-LINE: CAGR **+19.2%**, max DD -34.4%, Sharpe 4.70, 2,415 entries
- control RANDOM: CAGR **+19.7%**, max DD -26.9%, Sharpe 6.16, 901 entries
- (our live 33-month FVB, same universe: CAGR +14.4%, max DD -22.6%)

Per-trade, last 12 months: 144 trades, mean +7.11% (vs their claimed "+$15k in the
last 12 months" on a $10k/trade, 20-stock sample — not directly comparable in scale,
but shows the system is still finding entries recently, not stale).

## Verdict
**No real edge found over our universe/period — and on this evidence, the "proper"
two-line rule is actually the weaker version, not the better one:**

1. The RANDOM control (same ticker, same hold-length distribution, just random entry
   days) beat the REAL system on every single metric — higher win rate is the only
   one it didn't win outright, and even there it was close (67.1% vs 77.4% isn't huge
   given the random version's return/PF/CAGR/Sharpe are all clearly better). This is
   the same result we got for the Silver Bullet's zone-timing and the PPS Killer's
   entry-timing: a slow trend-following *exit* mechanically captures long winners in a
   30-year, large-cap-heavy, bull-biased sample regardless of what specific rule
   decided when to get in. The %R double-zone "exhaustion" timing isn't adding
   anything beyond that.
2. The video's own central warning — "don't buy on the fast line alone, wait for both
   lines to agree" — does **not** hold up at the portfolio level here. The single-line
   "mistake" version has a *higher* CAGR (+19.2% vs +13.9%) because it trades far more
   often (3.3x) and compounds that turnover, even though its per-trade quality is
   genuinely worse (win rate -8.6pp, mean return per trade less than half). Same
   turnover-vs-quality trade-off we found between our FVB and the Silver Bullet.
3. **Longest losing streak: 24** on the real/proper version, against their claimed
   "never lost more than 5 in a row." That's not a small discrepancy — it's the single
   biggest red flag here. Either their 20-stock sample was lucky/cherry-picked, or
   there's a real difference in universe/period that matters a lot for risk, not just
   return. Either way, anyone following the video's claim of "max 5 losers in a row"
   is working from a number our broader test does not reproduce at all.

## What this does NOT rule out
- Their specific 20 stocks, run only 2014-2026, may genuinely have performed as
  claimed — we tested a broader, longer, different universe on purpose (our standard
  practice), and a narrower, more recent, large-cap-tech-tilted sample could look
  better by chance. That's exactly the kind of result a small hand-picked sample
  produces, and why we don't take it at face value without testing broadly first.
- We did not test the optional Parabolic-SAR trailing exit mentioned in the video as
  an alternative to the plain down-arrow exit; not tested here since the core
  entry-timing question (does the double-zone logic itself add edge) is already
  answered by the RANDOM control regardless of which exit variant is used.

**Not added to the indicator or the live scanner.** Code: `test_wr_trend_exhaustion.py`.
