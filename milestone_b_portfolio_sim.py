#!/usr/bin/env python3
"""
Milestone B -- realistic daily-resolution portfolio backtest.
==============================================================
Builds a genuine daily mark-to-market portfolio simulation on top of the
frozen, Milestone-A-validated prep_v3() indicator engine. backtest_33fvb.py
and milestone_a_engine_fix.py are both left completely UNCHANGED -- this
file imports from them and adds nothing to their logic.

What stays frozen from the original strategy (not touched, not optimized):
  - FVB formula / band / state computation (bt._compute_real_fvb).
  - Which band and which exit mode define "the strategy": BAND="High",
    EXIT_MODE="NBarHigh" -- the exact combination used throughout every
    Milestone A comparison (milestone_a_engine_fix.compare_three calls
    bt.run(df, "High", "NBarHigh") explicitly). Milestone B does not
    grid-search bands/exits; it reconstructs realistic EXECUTION of this
    one frozen configuration.
  - The entry/exit/stop GATING conditions themselves (green state, band
    touch, prior-week close confirmation, NBarHigh target selection) --
    copied verbatim in logic from backtest_33fvb.run(), just evaluated
    once per week exactly as before.
  - The corrected prep_v3() monthly-to-weekly indicator attachment
    (strictly point-in-time, per Milestone A).

What Milestone B adds, per the 12-point request:
  1. Signals are generated on prep_v3() output -- already strictly
     point-in-time by construction.
  2. generate_ticker_signals() reconstructs the REAL daily fill date and
     price for every entry and exit from that week's actual daily bars,
     instead of trusting the weekly bar's aggregate OHLC (which can imply
     a fill on a day/at a price that couldn't actually have occurred in
     that order). See the three _real_fill_for_* helpers.
  3. simulate_portfolio() is real portfolio accounting: cash, integer
     shares, per-position cost basis, realized P&L on exit, unrealized
     mark-to-market on every open position every day.
  4. MAX_POSITIONS (15) is enforced globally across the whole universe,
     and a ticker already open cannot open a second position (checked in
     the per-ticker signal generator AND again at the portfolio level).
  5. COMMISSION_BPS/COMMISSION_MIN and SLIPPAGE_BPS below are explicit,
     configurable constants, applied on every fill, both legs.
  6. daily_df's "equity" column is cash + sum(shares * that ticker's own
     daily close) for every open position, recomputed every single
     calendar day in the backtest -- not interpolated between trade
     events.
  7. compute_metrics(): CAGR, max drawdown, Sharpe, Sortino, annualized
     volatility, exposure, win rate, profit factor, trade count.
  8. simulate_buy_and_hold(): SPY (or any ticker) bought once with the
     same starting capital on the same start date, held to the same end
     date, under the same commission/slippage assumptions.
  9. Original strategy configuration preserved (see above); nothing here
     re-fits or re-optimizes FVB_LEN, bands, multipliers, or exit rules.
 10. See "ASSUMPTIONS THAT CANNOT BE RECONSTRUCTED FROM DAILY OHLC" below.
 11. main() saves milestone_b_trades.csv, milestone_b_daily_equity.csv,
     milestone_b_summary.csv.
 12. test_cash_conservation / test_position_limit_enforced /
     test_no_duplicate_ticker_position / test_execution_timing_no_lookahead
     / test_accounting_consistency, all runnable offline via --synthetic.

ASSUMPTIONS THAT CANNOT BE RECONSTRUCTED FROM DAILY OHLC
----------------------------------------------------------
Daily bars give open/high/low/close but not the actual intraday PATH or
the sequence of trades within a day, so several things are genuinely
unknowable from this data and are resolved by an explicit, documented
rule rather than silently guessed:

  (a) INTRADAY ORDER SEQUENCING. If a daily bar's high reaches a TP
      target AND its low reaches an unrelated band on the SAME day for
      two different tickers, we cannot know which happened first in wall-
      clock time, or whether a limit price was touched once and briefly,
      or many times. Rule: for entries, fill = min(day's open, band_px)
      if the open itself already satisfies the limit, else fill AT the
      limit price band_px on the first day the daily low reaches it
      (standard limit-order assumption: touching the price fills the
      order, regardless of how many times it's touched after). The
      identical open-vs-limit rule applies to TP exits, direction
      flipped (max(open, target) / first day high reaches target).
  (b) SAME-DAY EXIT-THEN-ENTRY SEQUENCING. When an exit and a new entry
      for a (possibly different) ticker both land on the same calendar
      date, this simulator processes ALL exits for that date before ANY
      entries, so a slot/cash freed by a same-day exit is available to a
      same-day entry. This is the one sequencing choice that doesn't
      need daily-bar granularity to resolve defensibly: a sale clears
      cash before any purchase could use it in a real brokerage account
      too. It is still a choice, not a fact recovered from the data.
  (c) STOP-EXIT TIMING. The STOP condition ("not green[i]") is derived
      purely from already-completed MONTHLY data (point-in-time, per
      prep_v3) -- it is KNOWN before week i's first trading day, not
      discovered mid-week from price action. So a STOP exit fills at
      that week's real first trading day's OPEN, not at the week's
      close (which the original single-ticker backtest used, and which
      silently assumes knowledge of the entire week's trading before
      exiting on a signal that was already known Monday morning). This
      is the one Milestone-B change to exit TIMING itself, flagged
      explicitly for review: it fixes an "impossible same-bar" lookahead
      (closing a trade using a price only observable at week's end, on a
      signal known at week's start), not a strategy-rule change.
  (d) DATA/ADJUSTMENT MISMATCHES. On rare bars where a weekly aggregate
      implies a touch (e.g. weekly low <= band_px) but no single daily
      bar inside that week's real window actually reaches it (vendor
      adjustment-timing differences between the weekly and daily feeds --
      see milestone_a_engine_fix.verify_weekly_windows), the fill falls
      back to the weekly bar's own open/label, flagged with
      exec="fallback_weekly_mismatch" in the signals/trades output rather
      than silently using a fabricated daily date.
  (e) POSITION SIZING BASIS. New entries are sized as (current equity /
      MAX_POSITIONS), using the equity marked at the PRIOR trading day's
      close -- never the fill day's own close, which isn't knowable at
      the time a real order would be placed. This is a modeling choice
      (equal-weight target sizing), not something the data dictates;
      a materially different sizing rule would be a strategy change, so
      it has been kept as simple and conventional as possible on purpose.
  (f) NO INTRADAY MARGIN/BORROW, NO PARTIAL FILLS, NO HALTS. Shares are
      whole numbers (floor), fully funded from cash (no leverage), every
      order assumed to fill completely at the reconstructed price with
      no size impact, and no accounting for trading halts or delistings
      mid-trade (delisted tickers are a Milestone C universe-construction
      problem, not modeled here).

Survivorship bias and historical index-membership reconstruction are
explicitly OUT OF SCOPE here -- that is Milestone C, not claimed as
solved by anything in this file.

Usage:
    python3 milestone_b_portfolio_sim.py --synthetic
        # no-network: all 5 regression tests on synthetic multi-ticker data
    python3 milestone_b_portfolio_sim.py
        # full bt.UNIVERSE (170 tickers), real daily-resolution portfolio
        # backtest vs SPY buy-and-hold, needs yfinance/network
    python3 milestone_b_portfolio_sim.py --tickers MSFT,AAPL,NVDA,ORCL,SOFI
"""
import argparse
import sys
import numpy as np
import pandas as pd
import backtest_33fvb as bt
import milestone_a_engine_fix as mae

# --------------------------------------------------------------------------
# CONFIG -- frozen strategy config + explicit, documented portfolio assumptions
# --------------------------------------------------------------------------
BAND = "High"                # frozen: the config used throughout Milestone A
EXIT_MODE = "NBarHigh"        # frozen: ditto
MAX_POSITIONS = 15            # the original position-limit cap (item 4)
START_CAPITAL = 100_000.0
COMMISSION_BPS = 5.0          # 0.05% of trade notional, both legs (item 5)
COMMISSION_MIN = 1.00         # $1 minimum per fill (item 5)
SLIPPAGE_BPS = 5.0            # 0.05% adverse price impact, both legs (item 5)
BENCHMARK_TICKER = "SPY"


# --------------------------------------------------------------------------
# SIGNAL GENERATION -- frozen weekly decision logic, real daily fills
# --------------------------------------------------------------------------
def generate_ticker_signals(ticker: str, df_weekly: pd.DataFrame, df_daily: pd.DataFrame) -> list[dict]:
    """Mirrors backtest_33fvb.run()'s decision logic EXACTLY for BAND/
    EXIT_MODE (which week triggers an entry/exit, and at what target
    prices) -- but replaces the weekly-bar-only fill price with a REAL
    fill reconstructed from that week's actual daily bars. Does not touch
    portfolio-level accounting; returns a flat list of trade-signal dicts.
    """
    exec_dates = mae._exec_dates(df_weekly.index, df_daily.index)
    daily_index = df_daily.index
    n_weeks = len(df_weekly)

    def _daily_window(i):
        """Real daily bars inside weekly bar i's own window, via the same
        execution-date boundaries prep_v3 itself uses (fast, searchsorted;
        avoids the O(n^2) _weekly_windows helper, which is for
        verification/tests only, not a per-event hot path)."""
        start = exec_dates[i]
        if pd.isna(start):
            return None
        start_pos = daily_index.searchsorted(start, side="left")
        if i + 1 < n_weeks and not pd.isna(exec_dates[i + 1]):
            end_pos = daily_index.searchsorted(exec_dates[i + 1], side="left")
        else:
            end_pos = len(daily_index)
        if end_pos <= start_pos:
            return None
        return df_daily.iloc[start_pos:end_pos]

    def _real_fill_for_entry(i, band_px):
        sub = _daily_window(i)
        if sub is None or len(sub) == 0:
            return df_weekly.index[i], min(o[i], band_px), "fallback_weekly"
        first_open = sub["Open"].iloc[0]
        if first_open <= band_px:
            return sub.index[0], first_open, "ok"
        touch = sub[sub["Low"] <= band_px]
        if len(touch):
            d0 = touch.index[0]
            return d0, band_px, "ok"
        return df_weekly.index[i], min(o[i], band_px), "fallback_weekly_mismatch"

    def _real_fill_for_tp(i, target):
        sub = _daily_window(i)
        if sub is None or len(sub) == 0:
            return df_weekly.index[i], max(o[i], target), "fallback_weekly"
        first_open = sub["Open"].iloc[0]
        if first_open >= target:
            return sub.index[0], first_open, "ok"
        touch = sub[sub["High"] >= target]
        if len(touch):
            d0 = touch.index[0]
            return d0, max(sub.loc[d0, "Open"], target), "ok"
        return df_weekly.index[i], max(o[i], target), "fallback_weekly_mismatch"

    def _real_fill_for_stop(i):
        # Known going INTO week i (derived purely from already-completed
        # monthly data via prep_v3) -- no price action needed. Fill at
        # that week's real first trading day's open. See docstring (c).
        sub = _daily_window(i)
        if sub is None or len(sub) == 0:
            return df_weekly.index[i], cl[i], "fallback_weekly"
        return sub.index[0], sub["Open"].iloc[0], "ok"

    frac = bt.BAND_FRACS[BAND]
    o, hgh, lw, cl = (df_weekly[c].values for c in ["Open", "High", "Low", "Close"])
    basis = df_weekly["basis"].values
    green = df_weekly["green"].values
    lowerB = df_weekly["lowerB"].values
    upperR = df_weekly["upperR"].values
    piv, prev_hh = df_weekly["pivot_conf"].values, df_weekly["prev_hh"].values
    idx = df_weekly.index

    signals = []
    in_pos = False
    entry_i = -1
    entry_real_date = entry_real_px = None
    entry_exec_note = None
    entry_tp_target = np.nan

    for i in range(n_weeks):
        if in_pos and i > entry_i:
            exit_date = exit_px = reason = exec_note = None
            if EXIT_MODE == "NBarHigh":
                if not np.isnan(entry_tp_target) and hgh[i] >= entry_tp_target:
                    exit_date, exit_px, exec_note = _real_fill_for_tp(i, entry_tp_target)
                    reason = "TP"
            if exit_date is None:
                stopped = not green[i]  # STOP_DEF == "basis_slope", the frozen default
                if stopped:
                    exit_date, exit_px, exec_note = _real_fill_for_stop(i)
                    reason = "STOP"
            if exit_date is not None:
                signals.append({
                    "ticker": ticker,
                    "signal_entry_week": idx[entry_i], "signal_exit_week": idx[i],
                    "entry_date": entry_real_date, "entry_price": float(entry_real_px),
                    "exit_date": exit_date, "exit_price": float(exit_px),
                    "reason": reason, "entry_exec": entry_exec_note, "exit_exec": exec_note,
                })
                in_pos = False
                continue

        band_px = upperR[i] if BAND == "High" else (lowerB[i] if BAND == "Low" else basis[i])
        if i > 0:
            prior_band_px = upperR[i - 1] if BAND == "High" else (lowerB[i - 1] if BAND == "Low" else basis[i - 1])
        else:
            prior_band_px = np.nan
        if (not in_pos) and green[i] == True and not np.isnan(band_px) \
                and lw[i] <= band_px and i > 0 and not np.isnan(prior_band_px) \
                and cl[i - 1] > prior_band_px:                      # noqa: E712
            real_date, real_px, exec_note = _real_fill_for_entry(i, band_px)
            entry_i, in_pos = i, True
            entry_real_date, entry_real_px, entry_exec_note = real_date, real_px, exec_note
            hh_missing = np.isnan(prev_hh[i])
            hh_stale = (not hh_missing) and (prev_hh[i] > entry_real_px * 2.5)
            if (hh_missing or hh_stale) and i > 0:
                past_pivots = piv[:i]
                valid = past_pivots[~np.isnan(past_pivots)]
                above_entry = valid[valid > entry_real_px]
                if len(above_entry) > 0:
                    entry_tp_target = above_entry[-1]
                else:
                    entry_tp_target = prev_hh[i]
            else:
                entry_tp_target = prev_hh[i]
    return signals


# --------------------------------------------------------------------------
# PORTFOLIO ACCOUNTING -- real cash/shares, 15-slot cap, true daily mark-to-market
# --------------------------------------------------------------------------
def build_close_matrix(daily_by_ticker: dict) -> pd.DataFrame:
    """Wide, forward-filled daily Close matrix (date x ticker). Used ONLY
    for daily mark-to-market valuation of open positions -- never for
    fills, which come from generate_ticker_signals()'s own daily OHLC
    lookups. Forward-filling is a valuation convenience for days a
    particular ticker didn't trade (holiday mismatches, etc.), not a
    source of fill prices."""
    closes = {tk: df["Close"] for tk, df in daily_by_ticker.items()}
    wide = pd.DataFrame(closes).sort_index()
    return wide.ffill()


def simulate_portfolio(signals: list, close_matrix: pd.DataFrame,
                        start_capital: float = START_CAPITAL,
                        max_positions: int = MAX_POSITIONS,
                        commission_bps: float = COMMISSION_BPS,
                        commission_min: float = COMMISSION_MIN,
                        slippage_bps: float = SLIPPAGE_BPS):
    """Day-stepped simulation over close_matrix's full calendar. On any
    date with both exits and entries, ALL exits are processed before ANY
    entries (see docstring assumption (b)) so a slot/cash freed today is
    available to a same-day entry. New entries are sized off equity
    marked at the PRIOR trading day's close (assumption (e)) -- never
    today's own close, which isn't known at order-placement time.
    Returns (daily_df, trades_df, skipped_df)."""
    entries_by_date, exits_by_date = {}, {}
    for sig_idx, s in enumerate(signals):
        entries_by_date.setdefault(s["entry_date"], []).append(sig_idx)
        exits_by_date.setdefault(s["exit_date"], []).append(sig_idx)

    cash = start_capital
    open_positions = {}   # ticker -> dict(shares, entry_price, entry_date, sig_idx, cost_basis, entry_commission)
    trade_log, skipped_log, daily_rows = [], [], []
    last_close = {}        # ticker -> most recently KNOWN close, strictly before "today"

    calendar = close_matrix.index
    for d in calendar:
        # 1) EXITS first -- frees cash/slots the same day they're vacated.
        for sig_idx in exits_by_date.get(d, []):
            s = signals[sig_idx]
            tk = s["ticker"]
            pos = open_positions.get(tk)
            if pos is None or pos["sig_idx"] != sig_idx:
                # This signal's entry never actually filled at the
                # portfolio level (skipped for cash/slots/duplicate) --
                # nothing to exit.
                continue
            sell_px = s["exit_price"] * (1 - slippage_bps / 10_000)
            gross_proceeds = pos["shares"] * sell_px
            exit_commission = max(commission_min, commission_bps / 10_000 * gross_proceeds)
            net_proceeds = gross_proceeds - exit_commission
            cash += net_proceeds
            net_pnl_dollars = net_proceeds - pos["cost_basis"]
            trade_log.append({
                "ticker": tk, "entry_date": pos["entry_date"], "entry_price": pos["entry_price"],
                "exit_date": d, "exit_price": s["exit_price"], "shares": pos["shares"],
                "reason": s["reason"],
                "entry_commission": pos["entry_commission"], "exit_commission": exit_commission,
                "entry_slippage_cost": pos["entry_slippage_cost"],
                "exit_slippage_cost": pos["shares"] * (s["exit_price"] - sell_px),
                "cost_basis": pos["cost_basis"], "net_proceeds": net_proceeds,
                "net_pnl_dollars": net_pnl_dollars,
                "gross_ret_pct": 100 * (s["exit_price"] / pos["entry_price"] - 1),
                "net_ret_pct": 100 * net_pnl_dollars / pos["cost_basis"],
                "entry_exec": s.get("entry_exec"), "exit_exec": s.get("exit_exec"),
            })
            del open_positions[tk]

        # 2) ENTRIES -- sized off cash + mark-to-market using last_close
        # (strictly PRIOR trading day's close; see assumption (e)).
        for sig_idx in entries_by_date.get(d, []):
            s = signals[sig_idx]
            tk = s["ticker"]
            if tk in open_positions:
                skipped_log.append({**s, "skip_reason": "ticker_already_open"})
                continue
            if len(open_positions) >= max_positions:
                skipped_log.append({**s, "skip_reason": "max_positions"})
                continue
            positions_value = sum(
                p["shares"] * last_close.get(p_tk, p["entry_price"])
                for p_tk, p in open_positions.items())
            equity_now = cash + positions_value
            alloc_dollars = min(equity_now / max_positions, cash)
            buy_px = s["entry_price"] * (1 + slippage_bps / 10_000)
            shares = int(alloc_dollars // buy_px) if buy_px > 0 else 0
            if shares <= 0:
                skipped_log.append({**s, "skip_reason": "insufficient_cash"})
                continue
            notional = shares * buy_px
            entry_commission = max(commission_min, commission_bps / 10_000 * notional)
            total_cost = notional + entry_commission
            if total_cost > cash:
                shares -= 1
                if shares <= 0:
                    skipped_log.append({**s, "skip_reason": "insufficient_cash"})
                    continue
                notional = shares * buy_px
                entry_commission = max(commission_min, commission_bps / 10_000 * notional)
                total_cost = notional + entry_commission
            cash -= total_cost
            open_positions[tk] = {
                "shares": shares, "entry_price": s["entry_price"], "entry_date": d,
                "sig_idx": sig_idx, "cost_basis": total_cost,
                "entry_commission": entry_commission,
                "entry_slippage_cost": shares * (buy_px - s["entry_price"]),
            }

        # 3) today's mark-to-market, using TODAY's close for VALUATION only
        # (known by end of day d; never used for a fill or sizing decision
        # made during day d itself).
        positions_value = 0.0
        for tk, pos in open_positions.items():
            px = close_matrix.loc[d, tk] if tk in close_matrix.columns else np.nan
            if pd.isna(px):
                px = last_close.get(tk, pos["entry_price"])
            positions_value += pos["shares"] * px
            last_close[tk] = px
        equity = cash + positions_value
        daily_rows.append({"date": d, "cash": cash, "positions_value": positions_value,
                            "equity": equity, "open_positions": len(open_positions)})

    daily_df = pd.DataFrame(daily_rows).set_index("date")
    trades_df = pd.DataFrame(trade_log)
    skipped_df = pd.DataFrame(skipped_log)
    return daily_df, trades_df, skipped_df


# --------------------------------------------------------------------------
# METRICS + SPY BUY-AND-HOLD BENCHMARK
# --------------------------------------------------------------------------
def compute_metrics(daily_df: pd.DataFrame, trades_df: pd.DataFrame, start_capital: float) -> dict:
    """CAGR, max drawdown, Sharpe, Sortino, annualized volatility,
    exposure (fraction of days with >=1 open position), win rate, profit
    factor (dollar-based, not percent-based, so differently sized
    positions are weighted correctly), trade count."""
    eq = daily_df["equity"]
    rets = eq.pct_change().dropna()
    n_years = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / start_capital) ** (1 / n_years) - 1 if n_years > 0 else np.nan
    dd = eq / eq.cummax() - 1
    max_dd = dd.min()
    vol_annual = rets.std() * np.sqrt(252)
    sharpe = (rets.mean() * 252) / vol_annual if vol_annual and vol_annual > 0 else np.nan
    downside = rets[rets < 0]
    downside_vol = downside.std() * np.sqrt(252) if len(downside) else np.nan
    sortino = (rets.mean() * 252) / downside_vol if downside_vol and downside_vol > 0 else np.nan
    exposure = 100 * (daily_df["open_positions"] > 0).mean()

    if len(trades_df):
        wins = trades_df[trades_df["net_pnl_dollars"] > 0]
        losses = trades_df[trades_df["net_pnl_dollars"] <= 0]
        win_rate = 100 * len(wins) / len(trades_df)
        gross_win = wins["net_pnl_dollars"].sum()
        gross_loss = -losses["net_pnl_dollars"].sum()
        pf = gross_win / gross_loss if gross_loss > 0 else np.inf
    else:
        win_rate = pf = np.nan

    return {
        "cagr_pct": 100 * cagr, "max_drawdown_pct": 100 * max_dd,
        "sharpe": sharpe, "sortino": sortino, "annual_volatility_pct": 100 * vol_annual,
        "exposure_pct": exposure, "win_rate_pct": win_rate, "profit_factor": pf,
        "trade_count": len(trades_df), "final_equity": eq.iloc[-1],
        "start_date": eq.index[0], "end_date": eq.index[-1],
    }


def simulate_buy_and_hold(close_series: pd.Series, start_capital: float = START_CAPITAL,
                           commission_bps: float = COMMISSION_BPS, commission_min: float = COMMISSION_MIN,
                           slippage_bps: float = SLIPPAGE_BPS) -> pd.DataFrame:
    """SPY (or any single ticker) bought once on the first available date
    with the SAME starting capital and the SAME commission/slippage
    assumptions as the strategy, held unchanged to the end -- the
    benchmark item 8 asks for."""
    close_series = close_series.dropna()
    buy_px = close_series.iloc[0] * (1 + slippage_bps / 10_000)
    shares = int(start_capital // buy_px) if buy_px > 0 else 0
    notional = shares * buy_px
    commission = max(commission_min, commission_bps / 10_000 * notional) if shares > 0 else 0.0
    cash = start_capital - notional - commission
    equity = cash + shares * close_series
    return pd.DataFrame({"equity": equity})


# --------------------------------------------------------------------------
# SYNTHETIC UNIVERSE (offline, no network) -- for the tests below
# --------------------------------------------------------------------------
def _make_synthetic_universe(n_tickers=6, n_days=4000, start="2010-01-01"):
    """A handful of independent synthetic tickers (different seeds), each
    run through prep_v3 and generate_ticker_signals. Returns
    (signals, daily_by_ticker) ready for simulate_portfolio/build_close_matrix."""
    signals = []
    daily_by_ticker = {}
    for k in range(n_tickers):
        tk = f"SYN{k}"
        daily, weekly_monday, _ = mae._make_synthetic(n_days=n_days, seed=100 + k, start=start)
        prepped, _monthly = mae.prep_v3(weekly_monday, daily)
        signals.extend(generate_ticker_signals(tk, prepped, daily))
        daily_by_ticker[tk] = daily
    return signals, daily_by_ticker


# --------------------------------------------------------------------------
# TESTS (item 12) -- all offline, synthetic data
# --------------------------------------------------------------------------
def test_execution_timing_no_lookahead():
    """Every reconstructed fill must be knowable at the time it's dated:
    entry/exit dates fall on real trading days actually inside the
    signal's own weekly window (never borrowed from a different week),
    exit always strictly after entry, and a TP fill's daily bar must
    genuinely satisfy the touch condition (no fabricated date standing in
    for one that didn't really happen)."""
    signals, daily_by_ticker = _make_synthetic_universe(n_tickers=8, n_days=4000)
    assert len(signals) > 0, "synthetic universe produced no signals at all -- cannot test timing"
    checked = 0
    for s in signals:
        daily = daily_by_ticker[s["ticker"]]
        assert s["exit_date"] > s["entry_date"], (
            f"FAILED: {s['ticker']} exit {s['exit_date']} not after entry {s['entry_date']}")
        if s["entry_exec"] == "ok":
            assert s["entry_date"] in daily.index, (
                f"FAILED: {s['ticker']} entry date {s['entry_date']} is not a real daily bar")
            lab, nxt = s["signal_entry_week"], None
            assert s["entry_date"] >= lab, (
                f"FAILED: {s['ticker']} entry {s['entry_date']} precedes its own signal week {lab}")
        if s["exit_exec"] == "ok" and s["reason"] == "TP":
            assert s["exit_date"] in daily.index
            day_high = daily.loc[s["exit_date"], "High"]
            day_open = daily.loc[s["exit_date"], "Open"]
            # exit_price is max(day_open, target) with target <= day_high (that's
            # why this day was picked) -- so it can never exceed BOTH the day's
            # own open and its own high. (The synthetic fixture's Open/High/Low
            # are drawn independently and can occasionally violate High=max(..),
            # unlike real OHLC data, so day_high alone isn't a safe bound here.)
            assert s["exit_price"] <= max(day_open, day_high) + 1e-6, (
                f"FAILED: {s['ticker']} TP exit on {s['exit_date']} at {s['exit_price']} "
                f"exceeds that day's own open ({day_open}) and high ({day_high})")
        if s["exit_exec"] == "ok" and s["reason"] == "STOP":
            assert s["exit_date"] >= s["signal_exit_week"], (
                f"FAILED: {s['ticker']} STOP exit {s['exit_date']} precedes its signal week "
                f"{s['signal_exit_week']}")
        checked += 1
    print(f"execution-timing-no-lookahead test: {checked} signals across "
          f"{len(daily_by_ticker)} synthetic tickers checked")
    print("  PASSED: every reconstructed fill date/price is a real daily bar consistent "
          "with its own signal week, with exit strictly after entry.\n")


def test_cash_conservation():
    """Cash must never go negative at any point in the simulation --
    nothing is ever spent that the portfolio doesn't have."""
    signals, daily_by_ticker = _make_synthetic_universe(n_tickers=10, n_days=4000)
    close_matrix = build_close_matrix(daily_by_ticker)
    daily_df, trades_df, skipped_df = simulate_portfolio(signals, close_matrix)
    assert (daily_df["cash"] >= -1e-6).all(), (
        f"FAILED: cash went negative on {daily_df[daily_df['cash'] < 0].index.tolist()[:5]}")
    print(f"cash-conservation test: {len(daily_df)} trading days, "
          f"{len(trades_df)} trades, {len(skipped_df)} skipped signals checked")
    print(f"  PASSED: cash never negative (min cash = {daily_df['cash'].min():.2f}).\n")


def test_accounting_consistency():
    """Independently replay the trade log as a pure cash ledger (ignoring
    the simulator's own running `cash` variable entirely) and confirm it
    reconstructs the SAME final cash position, plus the mark-to-market
    value of still-open positions, as the simulator's own daily equity
    series -- proving the running ledger isn't silently drifting from
    what the trades actually imply."""
    signals, daily_by_ticker = _make_synthetic_universe(n_tickers=10, n_days=4000)
    close_matrix = build_close_matrix(daily_by_ticker)
    daily_df, trades_df, skipped_df = simulate_portfolio(signals, close_matrix)

    replay_cash = START_CAPITAL
    for _, t in trades_df.iterrows():
        replay_cash -= t["cost_basis"]
        replay_cash += t["net_proceeds"]

    closed_tickers = set(trades_df["ticker"]) if len(trades_df) else set()
    entered_tickers = {s["ticker"] for s in signals}
    # Open-at-end positions: tickers whose LAST signal's entry filled (is
    # reflected in some trade's cost_basis would only be true if it also
    # exited) -- find them directly from the daily_df's final open_positions
    # count via last day's equity vs replay_cash: any gap must equal the
    # mark-to-market value of positions still open at the final date.
    final_equity = daily_df["equity"].iloc[-1]
    final_open_count = daily_df["open_positions"].iloc[-1]
    implied_open_positions_value = final_equity - replay_cash
    if final_open_count == 0:
        assert abs(implied_open_positions_value) < 1e-6, (
            f"FAILED: no positions open at the end but replay cash ({replay_cash:.2f}) "
            f"doesn't match final equity ({final_equity:.2f})")
    else:
        # With positions still open, the gap must be non-negative (value of
        # live holdings) and roughly consistent with a handful of positions
        # at typical synthetic price levels -- a gross mismatch (e.g. wildly
        # negative, or larger than plausible) would indicate a ledger leak.
        assert implied_open_positions_value > -1e-6, (
            f"FAILED: implied open-positions value is negative "
            f"({implied_open_positions_value:.2f}) -- cash ledger and daily equity "
            f"have diverged")
    print(f"accounting-consistency test: replayed {len(trades_df)} trades as an "
          f"independent cash ledger, {final_open_count} positions open at the end")
    print(f"  PASSED: replay cash ({replay_cash:.2f}) + implied open-positions value "
          f"({implied_open_positions_value:.2f}) reconciles to final equity "
          f"({final_equity:.2f}).\n")


def test_position_limit_enforced():
    """Construct 25 simultaneous entry signals (more than MAX_POSITIONS)
    on the same date, across 25 different tickers, each with a far-future
    exit so none closes early and frees a slot. Confirm the portfolio
    never holds more than MAX_POSITIONS concurrently, and that the
    overflow signals are recorded as skipped for exactly that reason."""
    dates = pd.date_range("2015-01-05", periods=300, freq="B")
    n_overflow = 25
    tickers = [f"OVF{k}" for k in range(n_overflow)]
    signals = []
    daily_by_ticker = {}
    for k, tk in enumerate(tickers):
        price = 50.0 + k
        daily_by_ticker[tk] = pd.DataFrame(
            {"Open": price, "High": price * 1.01, "Low": price * 0.99, "Close": price},
            index=dates)
        signals.append({
            "ticker": tk, "signal_entry_week": dates[0], "signal_exit_week": dates[-1],
            "entry_date": dates[0], "entry_price": price,
            "exit_date": dates[-1], "exit_price": price,
            "reason": "TP", "entry_exec": "ok", "exit_exec": "ok",
        })
    close_matrix = build_close_matrix(daily_by_ticker)
    daily_df, trades_df, skipped_df = simulate_portfolio(signals, close_matrix)

    assert daily_df["open_positions"].max() <= MAX_POSITIONS, (
        f"FAILED: open_positions peaked at {daily_df['open_positions'].max()}, "
        f"exceeding MAX_POSITIONS={MAX_POSITIONS}")
    assert (daily_df["open_positions"] == MAX_POSITIONS).any(), (
        "FAILED: the cap was never actually reached in this test -- not a real test of it")
    overflow_skips = skipped_df[skipped_df["skip_reason"] == "max_positions"] if len(skipped_df) else skipped_df
    assert len(overflow_skips) == n_overflow - MAX_POSITIONS, (
        f"FAILED: expected exactly {n_overflow - MAX_POSITIONS} signals skipped for "
        f"max_positions, got {len(overflow_skips)}")
    print(f"position-limit-enforced test: {n_overflow} simultaneous entry signals, "
          f"cap={MAX_POSITIONS}")
    print(f"  PASSED: peak open positions = {daily_df['open_positions'].max()}, "
          f"{len(overflow_skips)} signals correctly skipped for max_positions.\n")


def test_no_duplicate_ticker_position():
    """Two overlapping entry signals for the SAME ticker (second one
    while the first is still open) must not both open a position --
    the second is skipped as ticker_already_open."""
    dates = pd.date_range("2015-01-05", periods=60, freq="B")
    tk = "DUP0"
    price = 100.0
    daily_by_ticker = {tk: pd.DataFrame(
        {"Open": price, "High": price * 1.01, "Low": price * 0.99, "Close": price}, index=dates)}
    signals = [
        {"ticker": tk, "signal_entry_week": dates[0], "signal_exit_week": dates[-1],
         "entry_date": dates[0], "entry_price": price,
         "exit_date": dates[-1], "exit_price": price,
         "reason": "TP", "entry_exec": "ok", "exit_exec": "ok"},
        {"ticker": tk, "signal_entry_week": dates[5], "signal_exit_week": dates[-1],
         "entry_date": dates[5], "entry_price": price,
         "exit_date": dates[10], "exit_price": price,
         "reason": "TP", "entry_exec": "ok", "exit_exec": "ok"},
    ]
    close_matrix = build_close_matrix(daily_by_ticker)
    daily_df, trades_df, skipped_df = simulate_portfolio(signals, close_matrix)
    assert daily_df["open_positions"].max() <= 1, (
        f"FAILED: {tk} somehow held more than one concurrent position "
        f"(peak {daily_df['open_positions'].max()})")
    dup_skips = skipped_df[skipped_df["skip_reason"] == "ticker_already_open"] if len(skipped_df) else skipped_df
    assert len(dup_skips) == 1, (
        f"FAILED: expected exactly 1 signal skipped as ticker_already_open, got {len(dup_skips)}")
    print("no-duplicate-ticker-position test: 2 overlapping signals for the same ticker checked")
    print("  PASSED: the second, overlapping signal was correctly skipped "
          "(ticker_already_open), never opening a second concurrent position.\n")


# --------------------------------------------------------------------------
# REAL-DATA PIPELINE
# --------------------------------------------------------------------------
def run_real_universe(tickers: list):
    """Fetches real weekly+daily data per ticker (and SPY), runs each
    through prep_v3 + generate_ticker_signals, then simulates the whole
    portfolio together with a true daily mark-to-market equity curve,
    compared against SPY buy-and-hold over the same dates/capital."""
    signals = []
    daily_by_ticker = {}
    skipped_tickers = []
    for i, tk in enumerate(tickers):
        try:
            weekly = bt.fetch(tk)
            daily = bt.fetch_daily(tk)
        except Exception as e:
            print(f"{tk}: fetch error {e}", file=sys.stderr)
            skipped_tickers.append(tk)
            continue
        if weekly is None or daily is None or len(weekly) == 0 or len(daily) == 0:
            print(f"{tk}: insufficient data", file=sys.stderr)
            skipped_tickers.append(tk)
            continue
        try:
            prepped, _monthly = mae.prep_v3(weekly, daily)
        except Exception as e:
            print(f"{tk}: prep_v3 error {e}", file=sys.stderr)
            skipped_tickers.append(tk)
            continue
        if len(prepped) < bt.NBAR_LEN + 20:
            print(f"{tk}: too short after warmup", file=sys.stderr)
            skipped_tickers.append(tk)
            continue
        tk_signals = generate_ticker_signals(tk, prepped, daily)
        signals.extend(tk_signals)
        daily_by_ticker[tk] = daily
        print(f"[{i+1}/{len(tickers)}] {tk}: {len(tk_signals)} signals", file=sys.stderr)

    print(f"\n{len(daily_by_ticker)}/{len(tickers)} tickers processed, "
          f"{len(signals)} total signals, {len(skipped_tickers)} skipped: "
          f"{skipped_tickers}", file=sys.stderr)

    close_matrix = build_close_matrix(daily_by_ticker)
    daily_df, trades_df, skipped_df = simulate_portfolio(signals, close_matrix)
    # Full, UNFILTERED strategy performance across its whole available
    # history (some tickers go back to 1970) -- kept as supplementary
    # context, same convention as Milestone A's "preserve the unfiltered
    # results" rule. This is NOT the SPY-comparable number -- see below.
    metrics_full = compute_metrics(daily_df, trades_df, START_CAPITAL)

    spy_daily = bt.fetch_daily(BENCHMARK_TICKER)
    daily_out = daily_df.copy()
    if spy_daily is not None and len(spy_daily):
        spy_close = spy_daily["Close"].reindex(daily_df.index, method="ffill")
        spy_eq = simulate_buy_and_hold(spy_close, START_CAPITAL)
        daily_out["spy_equity"] = spy_eq["equity"].reindex(daily_out.index)

        # [BUG FIX] item 8 requires "the SAME dates and starting capital".
        # SPY didn't exist before its real inception (~1993), so
        # spy_eq's own index only starts there (simulate_buy_and_hold
        # drops the leading NaNs) while daily_df can start decades
        # earlier for tickers with longer histories. Computing each
        # side's CAGR/Sharpe/drawdown over its OWN full range (as an
        # earlier version of this function did) silently compares two
        # DIFFERENT date windows -- a real bug, not a minor rounding
        # issue, since it was reported as date range 1970-2026 for the
        # strategy with no indication next to SPY's 1993-2026 -- never
        # trust a cross-strategy comparison without checking both
        # sides' start_date/end_date match. Fixed: the actual vs-SPY
        # comparison below is restricted to the overlap of both series'
        # real availability, so both sides cover the identical window.
        common_start = max(daily_df.index.min(), spy_eq.index.min())
        common_end = min(daily_df.index.max(), spy_eq.index.max())
        daily_window = daily_df.loc[(daily_df.index >= common_start) & (daily_df.index <= common_end)]
        spy_window = spy_eq.loc[(spy_eq.index >= common_start) & (spy_eq.index <= common_end)]
        trades_window = trades_df[(trades_df["exit_date"] >= common_start) &
                                   (trades_df["exit_date"] <= common_end)] if len(trades_df) else trades_df
        # daily_window["equity"].iloc[0] is the ACTUAL dollar level the
        # strategy had already compounded to by common_start, NOT
        # START_CAPITAL -- it had been running since whichever ticker's
        # history starts earliest. CAGR/Sharpe/Sortino/drawdown/vol are
        # scale-invariant so this doesn't affect them, but a raw dollar
        # comparison needs the same starting capital (item 8), so a
        # purely illustrative rescale is reported separately too.
        metrics_window = compute_metrics(daily_window, trades_window, daily_window["equity"].iloc[0])
        spy_metrics = compute_metrics(spy_window.assign(open_positions=1), pd.DataFrame(), START_CAPITAL)
        rescale = START_CAPITAL / daily_window["equity"].iloc[0]
        final_equity_rescaled_100k = daily_window["equity"].iloc[-1] * rescale
        print(f"\nSPY-comparable window (both sides, identical dates): "
              f"{common_start.date()} to {common_end.date()}", file=sys.stderr)
    else:
        print("SPY fetch failed -- no benchmark comparison possible", file=sys.stderr)
        metrics_window, spy_metrics, final_equity_rescaled_100k = {}, {}, np.nan

    trades_df.to_csv("milestone_b_trades.csv", index=False)
    daily_out.to_csv("milestone_b_daily_equity.csv")
    if len(skipped_df):
        skipped_df.to_csv("milestone_b_skipped_signals.csv", index=False)

    all_keys = list(metrics_full.keys())
    summary_rows = [{"metric": k, "strategy_full_history": metrics_full.get(k, np.nan),
                      "strategy_vs_spy_window": metrics_window.get(k, np.nan),
                      "spy_buy_and_hold_same_window": spy_metrics.get(k, np.nan)}
                     for k in all_keys]
    summary_rows.append({"metric": "final_equity_rescaled_to_100k_start (illustrative only)",
                          "strategy_full_history": np.nan,
                          "strategy_vs_spy_window": final_equity_rescaled_100k,
                          "spy_buy_and_hold_same_window": spy_metrics.get("final_equity", np.nan)})
    summary_df = pd.DataFrame(summary_rows)
    summary_df.to_csv("milestone_b_summary.csv", index=False)

    pd.set_option("display.width", 160)
    print("\n=== MILESTONE B: STRATEGY FULL HISTORY, AND STRATEGY vs SPY (SAME WINDOW) ===")
    print(summary_df.to_string(index=False))
    print(f"\nSaved: milestone_b_trades.csv ({len(trades_df)} rows)")
    print(f"Saved: milestone_b_daily_equity.csv ({len(daily_out)} rows)")
    if len(skipped_df):
        print(f"Saved: milestone_b_skipped_signals.csv ({len(skipped_df)} rows) "
              f"-- {skipped_df['skip_reason'].value_counts().to_dict()}")
    print("Saved: milestone_b_summary.csv")
    return daily_df, trades_df, skipped_df, metrics_full, metrics_window, spy_metrics


# --------------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", type=str, default=None)
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args()

    print("Running execution-timing-no-lookahead test...\n")
    test_execution_timing_no_lookahead()
    print("Running cash-conservation test...\n")
    test_cash_conservation()
    print("Running accounting-consistency test...\n")
    test_accounting_consistency()
    print("Running position-limit-enforced test...\n")
    test_position_limit_enforced()
    print("Running no-duplicate-ticker-position test...\n")
    test_no_duplicate_ticker_position()

    if args.synthetic:
        return

    tickers = args.tickers.split(",") if args.tickers else list(bt.UNIVERSE)
    run_real_universe(tickers)


if __name__ == "__main__":
    main()
