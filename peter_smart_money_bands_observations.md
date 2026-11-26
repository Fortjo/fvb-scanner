# Peter DiCarlo — "THT Smart Money Bands" observations

Research log, started 2026-11-26. Everything here was read directly from his public
X posts (@pdicarlotrader). It is observation, not his official documentation.

## Status of his indicators
- 2026-11-26: a user replied that "the THT fair value band stopped working on TradingView".
  Peter: "We have updated the indicator and made it better. It's also now only for our
  private academy members." The public 33FVB is gone; our own copy in this repo
  (tht_33fvb_smz_volpro_combined.pine) is unaffected.
- The private "Combined Signal System" (used for his fund) and the multi-timeframe BX
  (monthly BX drawn over the weekly chart) have been members-only since at least Feb 2026.

## Chart readings (indicator value printed on his chart)
| date (chart) | ticker | chart TF | reading | notes |
|---|---|---|---|---|
| 2026-11-25 | BE   | 1W | 161.25 | stepped (monthly) green band |
| 2026-11-25 | ARM  | 1W | 198.45 | stepped |
| 2026-11-25 | AVGO | 1M | 321.49 | |
| 2026-11-25 | AAOI | 1W | 76.01  | |
| 2026-11-22 (data to 11-20) | SMH | 1W | 431.23 | stepped monthly band, green |
| 2026-11-22 (data to 11-20) | SMH | 1W | 537.72 | second, smooth (weekly) band, RED (price below, falling) |
| 2026-11-25 | NVDA | 1W | 180.00 | stepped, green; zone edges read off chart ~198 / ~152; price 144 (below zone, band still green) |

## Reverse-engineered formula (check_smart_money_band_hypothesis.py)
Centre line = **20-period EMA of OHLC4**, monthly (stepped) or weekly (smooth).
Fitted on BE/ARM/AVGO (all within 0.1%); out-of-sample AAOI exact (76.01),
SMH weekly exact (537.72), SMH monthly +0.7% (likely screenshot timing).
**Shaded zone = the same median-deviation band method as our FVB, built around the
20-EMA instead of the 33-SMA** (check_smart_money_band_zone.py): NVDA ours 199.8 / 155.1
vs his ~198 / ~152; BE ours 196.9 / 137.3 vs his ~197 / ~137 (his edges read by pixel, +/- ~$3).
In other words: his new indicator = our FVB with the basis swapped from a 33-period simple
average to a 20-period exponential average. That is exactly variant A in our backtest.

Colour: consistent with the same sticky rule as our FVB — the band only turns red when a
whole candle (its high) closes below the lower edge. NVDA traded at 144, below the lower
edge (~155), and the band was still green; Peter: "If the band closes a month red, I will
change my outlook" (2026-11-25). For NVDA that needs a full month with its high under ~155.

## Other indicators visible on his charts (SMH, 2026-11-22)
THT Bull Cycle, SRSI Candles, THT Volume Pro, two THT Smart Money Bands (monthly + weekly),
THT Multi Timeframe BX (histogram, read -23.18), boxes labelled "Institutional Buy Zone"
and "Smart Money Zone".

## Rules and claims he stated
- Monthly BX = "most important indicator to me", then the weekly market bias (2026-01-30).
- BX only counts as red once the month has closed, not intra-month (2026-03-03).
- BX "is just an RSI in histogram format" (2026-04-12).
- 33FVB bands = average of OHLC over the past 33 candles (2026-01-26).
- Shorter-term BX used inside a bull cycle for entries/exits (2026-07-01); short-term
  setups only once the long-term setup is in play (2026-04-10).
- "Bull Cycle Over" = monthly BX turned red (LMND, 2026-04-06).
- Stops: "multiple weekly closes under the swing low" (META, $520, 2026-11-20);
  "two weekly closes below $660" (QQQ, 2026-11-22).
- Monthly Smart Money Band = "strongest support in our long term system", bounces
  "80% of the time" (BE, 2026-11-25); QQQ level "we never break, except 2008 and 2000".

## Our backtest of the 20-month line (test_ema20_band_system.py, 2026-11-26)
Same universe/engine/exits as our live 33-month FVB:
- Our 33-month FVB: 1,851 trades, 71% win, +16.0% avg, PF 3.58; portfolio CAGR 14.4%, max DD -22.6%.
- 20-month EMA, same band method: 3,136 trades, 79% win, +12.7% avg, PF 3.47; CAGR 13.4%, max DD -30.9%.
- Touch the 20-month line, EMA-slope stop: 3,425 trades, 58% win, +7.7% avg, PF 2.64; CAGR 14.3%, max DD -31.8%.
- SPY touches of the 20-month line since 1994: 16 episodes, 7 bounced (his claim 18/20) —
  the line alone does not reproduce his bounce count; his shaded zone/break rule must differ.
Verdict: keep the 33-month system; the 20-month line is not better on our engine.
(Variant A turned out to be his actual band construction — see zone check above — so this
is a direct test of his new band with our entry/exit rules, not an approximation.)

## "Silver Bullet" setup (from his posts, Oct-Nov 2026)
- He calls it "the strongest setup in my entire system" (DELL, 2026-10-27).
- Charts (TEM 2026-11-02, MDB 2026-11-03) are DAILY: price pulls back into the daily
  Smart Money Zone (the fib boxes: "Institutional Buy Zone" 0.618-0.786, "Smart Money Zone"
  0.786-0.826 -- same boxes as our SMZ module) while the long-term bull cycle is on
  (THT BX Dash showing monthly and weekly BX both positive: TEM 1M +17.5, W +5.9).
- Short-term target = prior swing high; examples: TEM +25%, OSCR +20%, DELL +11%, AMD and MDB
  taken into earnings. Exempt from his 5-point fundamentals checklist.
- Backtested 2026-11-26 (test_silver_bullet.py), 167 large caps, daily 1995-2026, rules fixed in
  advance (pivots 10/10, zone 0.618-0.826 of last swing, monthly+weekly BX > 0, target prior
  swing high, stop on a daily close below the swing low):
  - Silver Bullet: 4,196 trades, 48.5% win, +1.6% avg, PF 1.42, median hold 18 days.
  - Same rules without the BX filter: 45.3% win, +1.4% avg -- the bull-cycle filter helps a little
    per trade and halves portfolio drawdown (-28% vs -54%).
  - Random bull-cycle days with the same target/stop distances: +2.0% avg, PF 1.57 -> the zone
    timing itself adds no edge beyond the reward:risk geometry.
  - Portfolio (15 slots): CAGR 11.9%, max DD -28% vs our 33-month FVB 14.4% / -22.6%.
  - Losing years: 2002, 2008, 2011, 2022, 2026 (so far).
  Verdict: positive but mediocre, worse than our FVB; not added to the indicator. His own
  discretion (fundamentals, earnings, chart patterns) is not captured by these rules.
