#!/usr/bin/env python3
"""
Milestone C -- corrected event-driven engine + volatility-adjusted sizing.
===========================================================================
Validates a candidate (proposed by an external auditor/collaborator,
"ChatGPT") for volatility-adjusted position sizing on top of the frozen
33FVB strategy. Two structural bugs in the Milestone B engine are fixed
FIRST, because the sizing candidate cannot be validated on a simulator
that silently drops or mis-sequences real positions:

BUG #1 -- UNFINISHED POSITIONS SILENTLY DROPPED.
  milestone_b_portfolio_sim.generate_ticker_signals() only appends a
  signal to its output when an EXIT actually resolves (STOP or TP) within
  the ticker's available weekly history. A ticker that is still in a
  position (in_pos == True) when the data simply RUNS OUT -- which is
  guaranteed to happen for whichever tickers are currently held as of
  "today" in a 2026-dated backtest -- produces NO signal at all. The
  entry never reaches simulate_portfolio(): cash is never debited for
  it, it is invisible to open_positions_df, and the backtest's own
  reported final cash/equity silently excludes it. (This explains why
  the Milestone B real-universe run reported ZERO open positions at the
  end, despite the 15-slot cap binding constantly throughout the run --
  a materially suspicious result that should have been caught earlier.)

BUG #2 -- PORTFOLIO-LEVEL SKIPS SUPPRESS FUTURE SIGNALS.
  generate_ticker_signals() is called ONCE per ticker, in isolation,
  with no knowledge of portfolio-level constraints (MAX_POSITIONS,
  available cash). It assumes every entry it decides on is actually
  taken, and its own in_pos state machine will not look for a NEW entry
  opportunity for that ticker until the (fictional) trade's exit week.
  When simulate_portfolio() later skips that entry for real (slot full,
  or insufficient cash), the per-ticker signal generator has no way to
  find out -- it keeps "holding" a position the portfolio never actually
  entered, suppressing every real entry opportunity for that ticker until
  the phantom trade's exit week arrives, even though the real portfolio
  was never actually in the ticker at all and should have been free to
  act on any of those opportunities.

THE FIX: a single joint, event-driven engine (run_corrected_portfolio(),
below) that processes all tickers together, one real-calendar-week at a
time. Each ticker's own entry/exit GATING rules (identical to the frozen
strategy, copied verbatim in logic from backtest_33fvb.run() via
milestone_a_engine_fix.prep_v3()'s point-in-time indicators and
milestone_b_portfolio_sim.py's real-daily-fill reconstruction) are
evaluated every week regardless of portfolio state. The crucial
difference: the decision of whether a position actually OPENS (is there
a free slot, is there enough cash) is made THEN AND THERE, synchronously,
and the ticker's own in_pos flag is updated based on what REALLY
happened:
  - Accepted entry  -> in_pos = True, tracked exactly like before.
  - Rejected entry (skipped for max_positions/insufficient_cash)
    -> in_pos stays False. The VERY NEXT week, this ticker's entry
       condition is freshly re-evaluated from scratch -- exactly as it
       would be for a real trader who tried to buy, couldn't, and is
       simply watching for the next signal. (Fixes bug #2.)
  - A position still open when a ticker's data runs out is NEVER
    dropped: there is no separate "signals" list to omit it from in the
    first place. It stays in `open_positions` and is marked to market on
    every remaining calendar day exactly like any other open position,
    and is fully captured in the final open_positions_df. (Fixes bug #1.)

Everything else is UNCHANGED from the already-audited Milestone B logic,
copied/ported verbatim:
  - Entry gating: green[i]==True, weekly low <= upperR[i] (BAND="High"),
    prior week's close > prior week's upperR.
  - Exit gating: STOP (not green[i]) checked BEFORE TP (NBarHigh target),
    per the Milestone B round-2 audit fix.
  - Real daily-fill reconstruction (open-vs-touch convention), with NO
    fabricated fills -- an unverifiable weekly-implied trigger is logged
    to `failures` and skipped, never acted on.
  - Same-day exits processed before same-day entries.
  - Equal-weight sizing basis (equity_now / MAX_POSITIONS at the PRIOR
    close), now with an optional multiplicative sizing_fn on top (see
    below) -- commission/slippage/decrement-loop logic unchanged from
    Milestone B's audited version.

VOLATILITY-ADJUSTED POSITION SIZING (the candidate under test)
----------------------------------------------------------------
Candidate spec, exactly as given, NOT re-tuned here:
  - 20-trading-day annualized REALIZED PORTFOLIO volatility (computed
    from the engine's OWN daily equity return series -- not any single
    stock's volatility).
  - Target volatility: 20% annualized.
  - Exposure multiplier = min(1.0, max(0.30, 0.20 / realized_vol)).
  - Multiplier computed using ONLY information available strictly BEFORE
    the execution day: the daily equity history used for the 20-day
    return window ends on the trading day immediately before the
    transaction's own real fill date, never including that day's own
    return. See `catchup_before()` / the call ordering in
    run_corrected_portfolio() for exactly how this is enforced.
  - No leverage: the multiplier only ever SHRINKS the equal-weight
    budget (multiplier <= 1.0), and the existing cash cap
    (`budget = min(equity_now/MAX_POSITIONS, cash) * multiplier`,
    with `total_cost <= cash` enforced by the same decrement loop as
    Milestone B) means a position is never funded beyond available cash.
  - MAX_POSITIONS, COMMISSION_BPS/MIN, SLIPPAGE_BPS: unchanged from
    Milestone B.
  - Fewer than 20 trading days of portfolio history exist (true only at
    the very start of a run): the multiplier defaults to 1.0 (no
    adjustment -- identical to the baseline for that short warm-up
    stretch). This is a necessity, not a tuned choice, and is the same
    rule applied uniformly to both the baseline and sizing runs' warm-up
    period (the baseline simply always uses 1.0, so there is nothing to
    differ during warm-up either way).

HOW THE MULTIPLIER AFFECTS SIZING/CASH/SUBSEQUENT ENTRIES (documented
per the task's explicit request):
  - At the moment of an ACCEPTED entry, `budget` (the dollar amount
    targeted for this position) is multiplied by the vol-based factor
    BEFORE the existing share-count/decrement-loop logic runs. A smaller
    budget simply buys fewer shares at the same price -- the fill price,
    commission rate, and slippage rate are all unchanged; only the SIZE
    of the position changes.
  - A smaller position means a smaller `cost_basis` is debited from
    cash at entry, so MORE cash is left over per trade than the
    equal-weight baseline would leave. Because `budget` at the NEXT
    entry is `min(equity_now/MAX_POSITIONS, cash) * multiplier` and
    `equity_now` already reflects the extra un-spent cash from every
    prior smaller position, the strategy ends up holding MORE total cash
    (and a higher cash-to-equity ratio) whenever the multiplier is below
    1.0 for an extended stretch -- it does not re-deploy the "savings"
    into larger positions elsewhere; it simply sits in cash. This is a
    direct, mechanical consequence of applying the multiplier to only
    the budget calculation and nothing else, not a separate design
    choice.
  - The multiplier does NOT affect which signals fire, which exits
    happen, or MAX_POSITIONS (a smaller position still occupies exactly
    one of the 15 slots) -- it only affects notional size per slot. A
    position that still can't clear the commission-floor-adjusted
    decrement loop even at multiplier=1.0 is still skipped exactly as
    before (`insufficient_cash`); the multiplier can only make that MORE
    likely to happen (smaller budget), never less.

This sandbox has no network access -- all REAL-universe runs (bt.fetch,
bt.fetch_daily) must happen on the user's own machine. This file is
fully testable offline via `--synthetic` (regression tests for both
structural bug fixes and the sizing math), and the real-universe runner
mirrors milestone_b_portfolio_sim.py's own usage pattern.

Usage:
    python3 milestone_c_vol_sizing.py --synthetic
    python3 milestone_c_vol_sizing.py
        # full bt.UNIVERSE, both baseline and vol-sizing, needs network
    python3 milestone_c_vol_sizing.py --tickers MSFT,AAPL,NVDA,ORCL,SOFI
"""
import argparse
import sys
import numpy as np
import pandas as pd
import backtest_33fvb as bt
import milestone_a_engine_fix as mae
from milestone_b_portfolio_sim import build_close_matrix

# --------------------------------------------------------------------------
# CONFIG -- identical to Milestone B's frozen strategy + portfolio constants
# --------------------------------------------------------------------------
BAND = "High"
EXIT_MODE = "NBarHigh"
MAX_POSITIONS = 15
START_CAPITAL = 100_000.0
COMMISSION_BPS = 5.0
COMMISSION_MIN = 1.00
SLIPPAGE_BPS = 5.0
BENCHMARK_TICKER = "SPY"

# Volatility-sizing candidate parameters -- fixed as given, never tuned
# against evaluation results (requirement 9).
VOL_LOOKBACK_DAYS = 20
VOL_TARGET_ANNUAL = 0.20
VOL_MULT_MIN = 0.30
VOL_MULT_MAX = 1.00


def vol_sizing_multiplier(recent_equity: list, lookback: int = VOL_LOOKBACK_DAYS,
                           target_vol: float = VOL_TARGET_ANNUAL,
                           mult_min: float = VOL_MULT_MIN, mult_max: float = VOL_MULT_MAX) -> float:
    """recent_equity: the daily equity series' VALUES strictly up to and
    including yesterday (the caller is responsible for never including
    today's own, not-yet-known return -- see catchup_before() in
    run_corrected_portfolio()). Returns 1.0 (no adjustment) whenever
    there isn't yet a full lookback window of returns -- a necessary
    warm-up fallback, not a tuned choice, applied identically regardless
    of which run (baseline vs candidate) is active."""
    if len(recent_equity) < lookback + 1:
        return 1.0
    eq = pd.Series(recent_equity[-(lookback + 1):])
    rets = eq.pct_change().dropna()
    if len(rets) < lookback:
        return 1.0
    realized_vol = rets.std() * np.sqrt(252)
    if not np.isfinite(realized_vol) or realized_vol <= 0:
        return 1.0
    return float(min(mult_max, max(mult_min, target_vol / realized_vol)))


class _TickerState:
    """Precomputed per-ticker arrays + live cursor/position state for the
    joint event-driven walk. One instance per ticker, mutated in place by
    run_corrected_portfolio() as weeks are processed."""

    def __init__(self, ticker: str, df_weekly: pd.DataFrame, df_daily: pd.DataFrame):
        self.ticker = ticker
        self.dfd = df_daily
        self.exec_dates = mae._exec_dates(df_weekly.index, df_daily.index)
        self.n = len(df_weekly)
        self.idx = df_weekly.index
        self.o = df_weekly["Open"].values
        self.h = df_weekly["High"].values
        self.l = df_weekly["Low"].values
        self.c = df_weekly["Close"].values
        self.green = df_weekly["green"].values
        self.upperR = df_weekly["upperR"].values
        self.lowerB = df_weekly["lowerB"].values
        self.basis = df_weekly["basis"].values
        self.piv = df_weekly["pivot_conf"].values
        self.prev_hh = df_weekly["prev_hh"].values

        self.i = 0
        self.in_pos = False
        self.entry_i = -1
        self.tp_target = np.nan
        self.failures = []

    def daily_window(self, i: int):
        start = self.exec_dates[i]
        if pd.isna(start):
            return None
        start_pos = self.dfd.index.searchsorted(start, side="left")
        if i + 1 < self.n and not pd.isna(self.exec_dates[i + 1]):
            end_pos = self.dfd.index.searchsorted(self.exec_dates[i + 1], side="left")
        else:
            end_pos = len(self.dfd.index)
        if end_pos <= start_pos:
            return None
        return self.dfd.iloc[start_pos:end_pos]

    def log_failure(self, i: int, kind: str, level: float, sub):
        self.failures.append({
            "ticker": self.ticker, "week": self.idx[i], "kind": kind, "band_or_target": level,
            "weekly_open": self.o[i], "weekly_high": self.h[i], "weekly_low": self.l[i], "weekly_close": self.c[i],
            "daily_bars_found": 0 if sub is None else len(sub),
            "daily_first_open": None if sub is None or len(sub) == 0 else sub["Open"].iloc[0],
            "daily_last_close": None if sub is None or len(sub) == 0 else sub["Close"].iloc[-1],
            "daily_max_high": None if sub is None or len(sub) == 0 else sub["High"].max(),
            "daily_min_low": None if sub is None or len(sub) == 0 else sub["Low"].min(),
        })

    def check_exit(self, i: int):
        """STOP checked before TP (round-2 audit fix, unchanged). Returns
        (exit_date, exit_price, reason) or None (no exit this week, or an
        unverifiable trigger -- logged to self.failures)."""
        stopped = not self.green[i]
        if stopped:
            sub = self.daily_window(i)
            if sub is None or len(sub) == 0:
                self.log_failure(i, "stop", np.nan, sub)
                return None
            return sub.index[0], float(sub["Open"].iloc[0]), "STOP"
        if EXIT_MODE == "NBarHigh" and not np.isnan(self.tp_target) and self.h[i] >= self.tp_target:
            sub = self.daily_window(i)
            if sub is None or len(sub) == 0:
                self.log_failure(i, "tp", self.tp_target, sub)
                return None
            first_open = sub["Open"].iloc[0]
            if first_open >= self.tp_target:
                return sub.index[0], float(first_open), "TP"
            touch = sub[sub["High"] >= self.tp_target]
            if len(touch):
                d0 = touch.index[0]
                return d0, float(max(sub.loc[d0, "Open"], self.tp_target)), "TP"
            self.log_failure(i, "tp", self.tp_target, sub)
            return None
        return None

    def check_entry(self, i: int):
        """Returns (entry_date, entry_price, tp_target) or None (gating
        not satisfied, or an unverifiable trigger -- logged)."""
        band_px = self.upperR[i] if BAND == "High" else (self.lowerB[i] if BAND == "Low" else self.basis[i])
        prior_band_px = (self.upperR[i - 1] if BAND == "High" else
                         (self.lowerB[i - 1] if BAND == "Low" else self.basis[i - 1])) if i > 0 else np.nan
        if not (self.green[i] == True and not np.isnan(band_px) and self.l[i] <= band_px and i > 0  # noqa: E712
                and not np.isnan(prior_band_px) and self.c[i - 1] > prior_band_px):
            return None
        sub = self.daily_window(i)
        if sub is None or len(sub) == 0:
            self.log_failure(i, "entry", band_px, sub)
            return None
        first_open = sub["Open"].iloc[0]
        if first_open <= band_px:
            entry_date, entry_px = sub.index[0], float(first_open)
        else:
            touch = sub[sub["Low"] <= band_px]
            if len(touch) == 0:
                self.log_failure(i, "entry", band_px, sub)
                return None
            entry_date, entry_px = touch.index[0], float(band_px)

        hh_missing = np.isnan(self.prev_hh[i])
        hh_stale = (not hh_missing) and (self.prev_hh[i] > entry_px * 2.5)
        if (hh_missing or hh_stale) and i > 0:
            past_pivots = self.piv[:i]
            valid = past_pivots[~np.isnan(past_pivots)]
            above_entry = valid[valid > entry_px]
            tp_target = float(above_entry[-1]) if len(above_entry) > 0 else float(self.prev_hh[i])
        else:
            tp_target = float(self.prev_hh[i])
        return entry_date, entry_px, tp_target


def run_corrected_portfolio(tickers_prepped: dict, start_capital: float = START_CAPITAL,
                             max_positions: int = MAX_POSITIONS, commission_bps: float = COMMISSION_BPS,
                             commission_min: float = COMMISSION_MIN, slippage_bps: float = SLIPPAGE_BPS,
                             use_vol_sizing: bool = False):
    """The joint, event-driven, bug-fixed engine. tickers_prepped:
    {ticker: (df_weekly_prepped_via_prep_v3, df_daily)}. When
    use_vol_sizing is True, every accepted entry's budget is scaled by
    vol_sizing_multiplier() computed from the engine's own daily equity
    history strictly through the prior trading day; when False
    (baseline), the multiplier is always 1.0 -- identical to Milestone
    B's original equal-weight sizing, just run through the corrected
    engine. Returns (daily_df, trades_df, skipped_df, open_positions_df,
    failures_df)."""
    states = {tk: _TickerState(tk, w, d) for tk, (w, d) in tickers_prepped.items()}
    all_weeks = sorted(set().union(*(set(s.idx) for s in states.values()))) if states else []

    close_matrix = build_close_matrix({tk: d for tk, (w, d) in tickers_prepped.items()})
    calendar = close_matrix.index

    cash = start_capital
    open_positions = {}   # ticker -> dict(shares, entry_price, entry_date, cost_basis, entry_commission,
                           #               entry_slippage_cost, exposure_multiplier)
    trade_log, skipped_log, daily_rows = [], [], []
    last_close = {}
    cal_pos = 0

    def catchup_before(date):
        """Appends a daily row for every calendar day STRICTLY before
        `date` not yet appended, using the CURRENT (pre-today's-
        transactions) cash/open_positions state. This is what guarantees
        the vol-sizing lookback never sees today's own not-yet-known
        return."""
        nonlocal cal_pos
        while cal_pos < len(calendar) and calendar[cal_pos] < date:
            _append_row(calendar[cal_pos])
            cal_pos += 1

    def catchup_through(date):
        nonlocal cal_pos
        while cal_pos < len(calendar) and calendar[cal_pos] <= date:
            _append_row(calendar[cal_pos])
            cal_pos += 1

    def _append_row(d):
        positions_value = 0.0
        for tk, pos in open_positions.items():
            px = close_matrix.loc[d, tk] if tk in close_matrix.columns else np.nan
            if pd.isna(px):
                px = last_close.get(tk, pos["entry_price"])
            positions_value += pos["shares"] * px
            last_close[tk] = px
        daily_rows.append({"date": d, "cash": cash, "positions_value": positions_value,
                            "equity": cash + positions_value, "open_positions": len(open_positions)})

    for W in all_weeks:
        batch = []  # (real_date, priority[0=exit,1=entry], ticker, kind, price, info)
        for tk, s in states.items():
            if s.i >= s.n or s.idx[s.i] != W:
                continue
            i = s.i
            acted = False
            if s.in_pos and i > s.entry_i:
                res = s.check_exit(i)
                if res is not None:
                    exit_date, exit_px, reason = res
                    batch.append((exit_date, 0, tk, "exit", exit_px, reason))
                    acted = True
            if (not s.in_pos) and (not acted):
                res = s.check_entry(i)
                if res is not None:
                    entry_date, entry_px, tp_target = res
                    batch.append((entry_date, 1, tk, "entry", entry_px, tp_target))
            s.i += 1  # advance regardless of outcome -- this IS the bug #2 fix

        batch.sort(key=lambda x: (x[0], x[1]))
        for real_date, _prio, tk, kind, price, info in batch:
            catchup_before(real_date)
            s = states[tk]
            if kind == "exit":
                pos = open_positions.get(tk)
                if pos is None:
                    # Entry for this ticker's current trade was never
                    # actually accepted (rejected at the portfolio level
                    # some earlier week) -- nothing to exit. s.in_pos was
                    # already False in that case so this branch is only
                    # reached if state bookkeeping is ever inconsistent;
                    # defensive, should not occur in practice.
                    s.in_pos = False
                    continue
                reason = info
                sell_px = price * (1 - slippage_bps / 10_000)
                gross_proceeds = pos["shares"] * sell_px
                exit_commission = max(commission_min, commission_bps / 10_000 * gross_proceeds)
                net_proceeds = gross_proceeds - exit_commission
                cash += net_proceeds
                net_pnl_dollars = net_proceeds - pos["cost_basis"]
                trade_log.append({
                    "ticker": tk, "entry_date": pos["entry_date"], "entry_price": pos["entry_price"],
                    "exit_date": real_date, "exit_price": price, "shares": pos["shares"], "reason": reason,
                    "entry_commission": pos["entry_commission"], "exit_commission": exit_commission,
                    "entry_slippage_cost": pos["entry_slippage_cost"],
                    "exit_slippage_cost": pos["shares"] * (price - sell_px),
                    "cost_basis": pos["cost_basis"], "net_proceeds": net_proceeds,
                    "net_pnl_dollars": net_pnl_dollars,
                    "gross_ret_pct": 100 * (price / pos["entry_price"] - 1),
                    "net_ret_pct": 100 * net_pnl_dollars / pos["cost_basis"],
                    "exposure_multiplier": pos["exposure_multiplier"],
                })
                del open_positions[tk]
                s.in_pos = False
            else:
                tp_target = info
                if tk in open_positions:
                    skipped_log.append({"ticker": tk, "entry_date": real_date, "entry_price": price,
                                         "skip_reason": "ticker_already_open"})
                    continue
                if len(open_positions) >= max_positions:
                    skipped_log.append({"ticker": tk, "entry_date": real_date, "entry_price": price,
                                         "skip_reason": "max_positions"})
                    continue
                positions_value = sum(p["shares"] * last_close.get(ptk, p["entry_price"])
                                       for ptk, p in open_positions.items())
                equity_now = cash + positions_value
                if use_vol_sizing:
                    recent_equity = [r["equity"] for r in daily_rows]
                    mult = vol_sizing_multiplier(recent_equity)
                else:
                    mult = 1.0
                budget = min(equity_now / max_positions, cash) * mult
                buy_px = price * (1 + slippage_bps / 10_000)
                shares = int(budget // (buy_px * (1 + commission_bps / 10_000))) if buy_px > 0 else 0
                notional = entry_commission = total_cost = 0.0
                while shares > 0:
                    notional = shares * buy_px
                    entry_commission = max(commission_min, commission_bps / 10_000 * notional)
                    total_cost = notional + entry_commission
                    if total_cost <= budget + 1e-9:
                        break
                    shares -= 1
                if shares <= 0 or total_cost > cash + 1e-9:
                    skipped_log.append({"ticker": tk, "entry_date": real_date, "entry_price": price,
                                         "skip_reason": "insufficient_cash", "exposure_multiplier": mult})
                    continue  # s.in_pos stays False -- bug #2 fix: freely re-evaluated next week
                cash -= total_cost
                open_positions[tk] = {
                    "shares": shares, "entry_price": price, "entry_date": real_date,
                    "cost_basis": total_cost, "entry_commission": entry_commission,
                    "entry_slippage_cost": shares * (buy_px - price), "exposure_multiplier": mult,
                }
                s.in_pos = True
                s.entry_i = s.i - 1   # s.i was already advanced past this week above
                s.tp_target = tp_target

    if len(calendar):
        catchup_through(calendar[-1])

    daily_df = pd.DataFrame(daily_rows).set_index("date") if daily_rows else \
        pd.DataFrame(columns=["cash", "positions_value", "equity", "open_positions"])
    trades_df = pd.DataFrame(trade_log)
    skipped_df = pd.DataFrame(skipped_log)

    # Open-positions ledger -- bug #1 fix made visible: any position still
    # open when a ticker's own data ran out is captured here exactly like
    # any other still-open position, never silently missing.
    open_rows = []
    final_date = calendar[-1] if len(calendar) else None
    for tk, pos in open_positions.items():
        close_px = close_matrix.loc[final_date, tk] if (final_date is not None and tk in close_matrix.columns) else np.nan
        if pd.isna(close_px):
            close_px = last_close.get(tk, pos["entry_price"])
        open_rows.append({
            "ticker": tk, "shares": pos["shares"], "entry_date": pos["entry_date"],
            "entry_price": pos["entry_price"], "cost_basis": pos["cost_basis"],
            "entry_commission": pos["entry_commission"], "entry_slippage_cost": pos["entry_slippage_cost"],
            "exposure_multiplier": pos["exposure_multiplier"],
            "as_of_date": final_date, "current_close": close_px,
            "market_value": pos["shares"] * close_px,
            "unrealized_pnl_dollars": pos["shares"] * close_px - pos["cost_basis"],
        })
    open_positions_df = pd.DataFrame(open_rows)

    all_failures = [f for s in states.values() for f in s.failures]
    failures_df = pd.DataFrame(all_failures)

    return daily_df, trades_df, skipped_df, open_positions_df, failures_df


def reconcile_ledger(start_capital: float, trades_df: pd.DataFrame, open_positions_df: pd.DataFrame,
                      daily_df: pd.DataFrame) -> dict:
    """Identical formula to Milestone B's reconcile_ledger() -- repeated
    here so this file has no runtime dependency beyond build_close_matrix
    on milestone_b_portfolio_sim."""
    realized_cash_delta = 0.0
    if len(trades_df):
        realized_cash_delta = (trades_df["net_proceeds"] - trades_df["cost_basis"]).sum()
    open_cost_total = open_positions_df["cost_basis"].sum() if len(open_positions_df) else 0.0
    open_mv_total = open_positions_df["market_value"].sum() if len(open_positions_df) else 0.0
    reconciled_cash = start_capital + realized_cash_delta - open_cost_total
    reconciled_equity = reconciled_cash + open_mv_total
    actual_cash = daily_df["cash"].iloc[-1] if len(daily_df) else np.nan
    actual_equity = daily_df["equity"].iloc[-1] if len(daily_df) else np.nan
    return {
        "start_capital": start_capital,
        "realized_cash_delta_from_completed_trades": realized_cash_delta,
        "open_positions_cost_basis_total": open_cost_total,
        "open_positions_market_value_total": open_mv_total,
        "reconciled_cash": reconciled_cash, "actual_cash": actual_cash,
        "cash_discrepancy": reconciled_cash - actual_cash,
        "reconciled_equity": reconciled_equity, "actual_equity": actual_equity,
        "equity_discrepancy": reconciled_equity - actual_equity,
    }


def compute_metrics(daily_df: pd.DataFrame, trades_df: pd.DataFrame, open_positions_df: pd.DataFrame,
                     start_capital: float) -> dict:
    """Same metric set as Milestone B's compute_metrics(), PLUS Calmar
    (CAGR / |max drawdown|) and an annualized one-way turnover estimate
    (requirement 8). Turnover definition, stated explicitly since there
    is no single universal convention: the sum of every position's entry
    notional (completed trades' cost_basis plus any still-open position's
    cost_basis, so a position open at the end still counts as one real
    buy) divided by the average daily equity over the period, divided by
    the number of years -- "what fraction of average portfolio value is
    bought anew per year, on average.\""""
    eq = daily_df["equity"]
    rets = eq.pct_change().dropna()
    n_years = (eq.index[-1] - eq.index[0]).days / 365.25 if len(eq) > 1 else np.nan
    cagr = (eq.iloc[-1] / start_capital) ** (1 / n_years) - 1 if n_years and n_years > 0 else np.nan
    dd = eq / eq.cummax() - 1
    max_dd = dd.min() if len(dd) else np.nan
    vol_annual = rets.std() * np.sqrt(252) if len(rets) else np.nan
    sharpe = (rets.mean() * 252) / vol_annual if vol_annual and vol_annual > 0 else np.nan
    downside = rets[rets < 0]
    downside_vol = downside.std() * np.sqrt(252) if len(downside) else np.nan
    sortino = (rets.mean() * 252) / downside_vol if downside_vol and downside_vol > 0 else np.nan
    calmar = cagr / abs(max_dd) if max_dd and max_dd < 0 else np.nan
    exposure = 100 * (daily_df["open_positions"] > 0).mean() if len(daily_df) else np.nan

    if len(trades_df):
        wins = trades_df[trades_df["net_pnl_dollars"] > 0]
        losses = trades_df[trades_df["net_pnl_dollars"] <= 0]
        win_rate = 100 * len(wins) / len(trades_df)
        gross_win = wins["net_pnl_dollars"].sum()
        gross_loss = -losses["net_pnl_dollars"].sum()
        pf = gross_win / gross_loss if gross_loss > 0 else np.inf
    else:
        win_rate = pf = np.nan

    total_entry_notional = (trades_df["cost_basis"].sum() if len(trades_df) else 0.0) + \
                            (open_positions_df["cost_basis"].sum() if len(open_positions_df) else 0.0)
    avg_equity = eq.mean() if len(eq) else np.nan
    turnover_pct_annualized = (100 * total_entry_notional / avg_equity / n_years
                                if avg_equity and avg_equity > 0 and n_years and n_years > 0 else np.nan)

    return {
        "cagr_pct": 100 * cagr if cagr == cagr else np.nan, "max_drawdown_pct": 100 * max_dd if max_dd == max_dd else np.nan,
        "sharpe": sharpe, "sortino": sortino, "calmar": calmar,
        "annual_volatility_pct": 100 * vol_annual if vol_annual == vol_annual else np.nan,
        "exposure_pct": exposure, "win_rate_pct": win_rate, "profit_factor": pf,
        "turnover_pct_annualized": turnover_pct_annualized,
        "trade_count": len(trades_df), "open_at_end_count": len(open_positions_df),
        "final_equity": eq.iloc[-1] if len(eq) else np.nan,
        "start_date": eq.index[0] if len(eq) else None, "end_date": eq.index[-1] if len(eq) else None,
    }


# --------------------------------------------------------------------------
# TESTS (all offline, synthetic/hand-built data)
# --------------------------------------------------------------------------
def _make_synthetic_universe(n_tickers=8, n_days=4000, start="2010-01-01"):
    tickers_prepped = {}
    for k in range(n_tickers):
        tk = f"SYN{k}"
        daily, weekly_monday, _ = mae._make_synthetic(n_days=n_days, seed=100 + k, start=start)
        prepped, _monthly = mae.prep_v3(weekly_monday, daily)
        tickers_prepped[tk] = (prepped, daily)
    return tickers_prepped


def test_vol_sizing_multiplier_formula():
    """Unit test of vol_sizing_multiplier() in isolation against a hand
    computed value, decoupled from the full engine."""
    # Flat equity -> zero realized vol -> function must fall back to 1.0
    # (division by ~0 would otherwise blow up), not crash or return inf.
    flat = [100_000.0] * 25
    assert vol_sizing_multiplier(flat) == 1.0, "FAILED: zero-vol edge case must fall back to 1.0, not crash/blow up"

    # Insufficient history (< lookback+1 points) -> must fall back to 1.0.
    short = [100_000.0 * (1.001 ** i) for i in range(15)]
    assert vol_sizing_multiplier(short) == 1.0, "FAILED: insufficient history must fall back to 1.0"

    # A deterministic, alternating +2%/-2% daily return series has a known
    # std; compute the expected multiplier by hand with the exact same
    # formula and compare.
    rng = np.random.default_rng(7)
    rets = rng.normal(0.0, 0.03, 21)  # ~3% daily vol -> annualized ~47.6%, above the 20% target
    eq = [100_000.0]
    for r in rets:
        eq.append(eq[-1] * (1 + r))
    mult = vol_sizing_multiplier(eq)
    rets_series = pd.Series(eq[-21:]).pct_change().dropna()
    realized_vol = rets_series.std() * np.sqrt(252)
    expected = min(1.0, max(0.30, 0.20 / realized_vol))
    assert abs(mult - expected) < 1e-12, (
        f"FAILED: multiplier {mult} doesn't match hand-computed {expected} "
        f"(realized_vol={realized_vol:.4f})")
    assert mult < 1.0, "FAILED: this fixture's ~48% annualized vol should produce a sub-1.0 multiplier"
    print(f"vol-sizing-multiplier-formula test: zero-vol, short-history, and a ~{100*realized_vol:.1f}%-vol "
          f"fixture checked")
    print(f"  PASSED: multiplier {mult:.4f} matches the hand-computed value exactly; "
          f"edge cases fall back to 1.0 as documented.\n")


def test_unfinished_position_retained_at_end():
    """[BUG #1 FIX] A position still open when the ticker's own data
    simply runs out (no STOP, no TP ever resolves before the end of the
    dataset) must NOT be silently dropped. It must appear in
    open_positions_df, be reflected in daily_df's final equity via
    mark-to-market, and never show up in trades_df (since it never
    closed)."""
    weekly_dates = pd.to_datetime(["2015-01-05", "2015-01-12", "2015-01-19", "2015-01-26", "2015-02-02"])
    daily_dates = pd.bdate_range("2015-01-05", periods=25)  # 5 weeks x 5 business days

    def flat_week(open_px, days):
        return pd.DataFrame({"Open": open_px, "High": open_px * 1.01, "Low": open_px * 0.99,
                              "Close": open_px}, index=days)

    # Week1 is the entry week (fills at day0's open, 101.5). Weeks 2-4
    # never satisfy STOP (green stays True) or TP (High never reaches
    # the target) -- the position is still open when the data ends.
    daily_df_input = pd.concat([
        flat_week(100.0, daily_dates[0:5]),
        flat_week(101.5, daily_dates[5:10]),
        flat_week(103.0, daily_dates[10:15]),
        flat_week(104.0, daily_dates[15:20]),
        flat_week(105.0, daily_dates[20:25]),
    ])

    weekly_df = pd.DataFrame({
        "Open": [100.0, 101.5, 103.0, 104.0, 105.0],
        "High": [106.0, 106.0, 104.0, 105.0, 106.0],
        "Low": [99.0, 101.0, 102.0, 103.0, 104.0],
        "Close": [105.0, 105.5, 103.5, 104.5, 105.5],
        "basis": [np.nan] * 5,
        "upperR": [100.0, 102.0, 102.0, 102.0, 102.0],
        "lowerR": [np.nan] * 5,
        "green": [1.0, 1.0, 1.0, 1.0, 1.0],       # always green -> never STOPs
        "lowerB": [np.nan] * 5,
        "pivot_conf": [np.nan] * 5,
        "prev_hh": [np.nan, 500.0, 500.0, 500.0, 500.0],  # TP target far out of reach -> never TPs
    }, index=weekly_dates)

    daily_df, trades_df, skipped_df, open_positions_df, failures_df = run_corrected_portfolio(
        {"NEVEREXIT0": (weekly_df, daily_df_input)}, max_positions=1)

    assert len(trades_df) == 0, (
        f"FAILED: expected 0 completed trades (position never exits), got {len(trades_df)}")
    assert len(open_positions_df) == 1, (
        f"FAILED: expected the still-open position to appear in open_positions_df, got "
        f"{len(open_positions_df)} rows -- this is exactly bug #1 (silent drop) if 0")
    assert open_positions_df["ticker"].iloc[0] == "NEVEREXIT0"
    assert open_positions_df["cost_basis"].iloc[0] > 0
    final_equity = daily_df["equity"].iloc[-1]
    final_cash = daily_df["cash"].iloc[-1]
    expected_mv = open_positions_df["market_value"].iloc[0]
    assert abs(final_equity - (final_cash + expected_mv)) < 1e-6, (
        "FAILED: final equity doesn't equal final cash + the open position's market value")
    assert daily_df["open_positions"].iloc[-1] == 1, (
        "FAILED: daily_df's own open_positions count at the final date should be 1")
    print("unfinished-position-retained-at-end test: a position with no resolved exit before data's end, checked")
    print(f"  PASSED: 0 completed trades, 1 open position retained and marked to market "
          f"(cost_basis={open_positions_df['cost_basis'].iloc[0]:.2f}, "
          f"market_value={expected_mv:.2f}); final equity ties out exactly.\n")


def test_portfolio_skip_does_not_suppress_future_entry():
    """[BUG #2 FIX] Two tickers both satisfy the entry condition in the
    same week, but max_positions=1 means only one slot exists. Ticker A
    is deliberately given an earlier real fill date so it takes the
    slot; ticker B's entry is rejected (skip_reason=max_positions).
    Under the OLD (buggy) per-ticker-isolated design, B's signal
    generator would have believed its entry succeeded and would not
    re-check for a new entry until that (fictional) trade's own exit --
    which was never even defined, since the trade never really happened.
    Under the fix, B's in_pos stays False after the rejection, so its
    OWN entry condition is freely re-evaluated the very next week it's
    satisfied. A exits in week 2 (freeing the slot); B's entry condition
    is deliberately re-satisfied, fresh, in week 3 -- this test confirms
    B's SECOND attempt is evaluated and accepted."""
    weekly_dates = pd.to_datetime(["2015-01-05", "2015-01-12", "2015-01-19", "2015-01-26"])
    daily_dates = pd.bdate_range("2015-01-05", periods=20)

    # Ticker A: entry week1 (fills day0, price 100.5), STOP exit week2
    # (frees the slot).
    a_week0 = pd.DataFrame({"Open": 99.0, "High": 100.0, "Low": 98.0, "Close": 99.5}, index=daily_dates[0:5])
    a_week1 = pd.DataFrame({"Open": [100.5, 101.0, 101.5, 102.0, 102.5],
                             "High": [101.0, 101.5, 102.0, 102.5, 103.0],
                             "Low": [100.0, 100.5, 101.0, 101.5, 102.0],
                             "Close": [100.8, 101.3, 101.8, 102.3, 102.8]}, index=daily_dates[5:10])
    a_week2 = pd.DataFrame({"Open": 90.0, "High": 91.0, "Low": 89.0, "Close": 90.5}, index=daily_dates[10:15])
    a_week3 = pd.DataFrame({"Open": 90.0, "High": 91.0, "Low": 89.0, "Close": 90.5}, index=daily_dates[15:20])
    a_daily = pd.concat([a_week0, a_week1, a_week2, a_week3])
    a_weekly = pd.DataFrame({
        "Open": [99.0, 100.5, 90.0, 90.0], "High": [100.0, 103.0, 91.0, 91.0],
        "Low": [98.0, 100.0, 89.0, 89.0], "Close": [99.5, 102.8, 90.5, 90.5],
        "basis": [np.nan] * 4, "upperR": [96.0, 102.0, np.nan, np.nan], "lowerR": [np.nan] * 4,
        "green": [1.0, 1.0, 0.0, 1.0],   # week2: STOP (green flips False)
        "lowerB": [np.nan] * 4, "pivot_conf": [np.nan] * 4, "prev_hh": [np.nan, 150.0, np.nan, np.nan],
    }, index=weekly_dates)

    # Ticker B: entry condition satisfied in week1 TOO, but its real fill
    # is day1 (one real day LATER than A's day0 fill) -- guarantees A is
    # processed first within the week's batch and takes the single slot.
    # B's week1 entry is rejected. Week2: B's own entry gating is
    # deliberately NOT satisfied (green=False) -- distinct from A's STOP,
    # just making sure B doesn't spuriously fire again immediately.
    # Week3: B's entry gating is satisfied AGAIN, fresh, with its own
    # real fill on day0 of week3.
    b_week0 = pd.DataFrame({"Open": 199.0, "High": 200.0, "Low": 198.0, "Close": 199.5}, index=daily_dates[0:5])
    b_week1 = pd.DataFrame({"Open": [103.0, 101.8, 102.0, 102.5, 103.0],   # day0 open=103 > band(102) -> no fill yet
                             "High": [104.0, 103.0, 103.0, 103.5, 104.0],
                             "Low": [102.5, 101.8, 101.5, 102.0, 102.5],   # day1's low 101.8 <= band 102 -> fills day1
                             "Close": [103.5, 102.5, 102.5, 103.0, 103.5]}, index=daily_dates[5:10])
    b_week2 = pd.DataFrame({"Open": 103.0, "High": 104.0, "Low": 102.5, "Close": 103.5}, index=daily_dates[10:15])
    b_week3 = pd.DataFrame({"Open": [95.5, 96.0, 96.5, 97.0, 97.5],
                             "High": [97.0, 97.5, 98.0, 98.0, 98.0],
                             "Low": [95.0, 95.5, 96.0, 96.5, 97.0],
                             "Close": [96.0, 96.5, 97.0, 97.5, 98.0]}, index=daily_dates[15:20])
    b_daily = pd.concat([b_week0, b_week1, b_week2, b_week3])
    b_weekly = pd.DataFrame({
        "Open": [199.0, 103.0, 103.0, 95.5], "High": [200.0, 104.0, 104.0, 98.0],
        "Low": [198.0, 101.8, 102.5, 95.0], "Close": [199.5, 103.5, 103.5, 98.0],
        "basis": [np.nan] * 4, "upperR": [197.0, 102.0, 95.0, 97.0], "lowerR": [np.nan] * 4,
        "green": [1.0, 1.0, 0.0, 1.0],   # week2: green False -> entry gating fails regardless of band/low
        "lowerB": [np.nan] * 4, "pivot_conf": [np.nan] * 4, "prev_hh": [np.nan, 150.0, 150.0, 150.0],
    }, index=weekly_dates)

    daily_df, trades_df, skipped_df, open_positions_df, failures_df = run_corrected_portfolio(
        {"A": (a_weekly, a_daily), "B": (b_weekly, b_daily)}, max_positions=1)

    b_skips = skipped_df[(skipped_df["ticker"] == "B") & (skipped_df["skip_reason"] == "max_positions")] \
        if len(skipped_df) else skipped_df
    assert len(b_skips) == 1, (
        f"FAILED: expected exactly 1 max_positions skip for B (its week1 attempt), got {len(b_skips)}")
    assert b_skips["entry_date"].iloc[0] == daily_dates[6], (
        f"FAILED: B's rejected entry should be dated {daily_dates[6]} (week1 day1), "
        f"got {b_skips['entry_date'].iloc[0]}")

    # B's SECOND attempt (week3) must have been evaluated and accepted --
    # this is the actual bug #2 fix under test. Under the old design, B
    # would still believe it was "in position" from the rejected week1
    # entry and would never even attempt this.
    b_trades = trades_df[trades_df["ticker"] == "B"] if len(trades_df) else trades_df
    b_open = open_positions_df[open_positions_df["ticker"] == "B"] if len(open_positions_df) else open_positions_df
    assert len(b_trades) == 1 or len(b_open) == 1, (
        f"FAILED: B's second (week3) entry attempt was never accepted -- bug #2 is NOT fixed. "
        f"trades={len(b_trades)}, open={len(b_open)}")
    b_entry_date = (b_trades["entry_date"].iloc[0] if len(b_trades) else b_open["entry_date"].iloc[0])
    assert b_entry_date == daily_dates[15], (
        f"FAILED: B's accepted entry should be dated {daily_dates[15]} (week3 day0), got {b_entry_date}")
    print("portfolio-skip-does-not-suppress-future-entry test: B rejected at max_positions in week1, "
          "re-evaluated and accepted fresh in week3, checked")
    print("  PASSED: B's week1 entry was correctly rejected (slot taken by A), and B's own entry "
          "gating was freely re-evaluated afterward, accepting its week3 opportunity -- not stuck "
          "waiting on a trade that never actually happened.\n")


def test_corrected_engine_cash_conservation():
    """Cash must never go negative at any point, across a larger
    synthetic multi-ticker universe, same as Milestone B's own test."""
    tickers_prepped = _make_synthetic_universe(n_tickers=10, n_days=4000)
    daily_df, trades_df, skipped_df, open_positions_df, failures_df = run_corrected_portfolio(tickers_prepped)
    assert (daily_df["cash"] >= -1e-6).all(), (
        f"FAILED: cash went negative (min={daily_df['cash'].min():.6f})")
    recon = reconcile_ledger(START_CAPITAL, trades_df, open_positions_df, daily_df)
    assert abs(recon["cash_discrepancy"]) < 1e-6 and abs(recon["equity_discrepancy"]) < 1e-6, (
        f"FAILED: reconciliation doesn't tie out ({recon['cash_discrepancy']}, {recon['equity_discrepancy']})")
    print(f"corrected-engine-cash-conservation test: {len(daily_df)} trading days, {len(trades_df)} completed "
          f"trades, {len(open_positions_df)} open at the end checked")
    print(f"  PASSED: cash never negative (min={daily_df['cash'].min():.2f}); reconciliation ties out to ~0.\n")


def test_vol_sizing_shrinks_positions_vs_baseline():
    """Integration-level check: running the SAME synthetic universe
    through the SAME corrected engine with use_vol_sizing=True must never
    produce a LARGER average position size than the baseline
    (use_vol_sizing=False) -- the multiplier only ever shrinks (<=1.0),
    never grows, the equal-weight budget. Also confirms both runs
    individually reconcile and never go cash-negative."""
    tickers_prepped = _make_synthetic_universe(n_tickers=10, n_days=4000)
    base = run_corrected_portfolio(tickers_prepped, use_vol_sizing=False)
    cand = run_corrected_portfolio(tickers_prepped, use_vol_sizing=True)
    base_daily, base_trades, base_skipped, base_open, _ = base
    cand_daily, cand_trades, cand_skipped, cand_open, _ = cand

    for label, (daily_df, trades_df, open_df) in [("baseline", (base_daily, base_trades, base_open)),
                                                    ("vol-sizing", (cand_daily, cand_trades, cand_open))]:
        assert (daily_df["cash"] >= -1e-6).all(), f"FAILED: {label} cash went negative"
        recon = reconcile_ledger(START_CAPITAL, trades_df, open_df, daily_df)
        assert abs(recon["cash_discrepancy"]) < 1e-6 and abs(recon["equity_discrepancy"]) < 1e-6, (
            f"FAILED: {label} reconciliation doesn't tie out")

    base_avg_cost = base_trades["cost_basis"].mean() if len(base_trades) else np.nan
    cand_avg_cost = cand_trades["cost_basis"].mean() if len(cand_trades) else np.nan
    assert len(base_trades) > 0 and len(cand_trades) > 0, "FAILED: fixture produced no trades to compare"
    assert cand_avg_cost <= base_avg_cost * 1.0001, (
        f"FAILED: vol-sizing average position cost ({cand_avg_cost:.2f}) exceeds baseline's "
        f"({base_avg_cost:.2f}) -- the multiplier should never INCREASE position size")
    assert (cand_trades["exposure_multiplier"] <= 1.0001).all(), (
        "FAILED: a vol-sizing trade's own recorded exposure_multiplier exceeds 1.0")
    assert (base_trades["exposure_multiplier"] == 1.0).all(), (
        "FAILED: baseline trades should all carry exposure_multiplier == 1.0 exactly")
    print(f"vol-sizing-shrinks-positions-vs-baseline test: baseline {len(base_trades)} trades "
          f"(avg cost_basis {base_avg_cost:.2f}) vs vol-sizing {len(cand_trades)} trades "
          f"(avg cost_basis {cand_avg_cost:.2f}) checked")
    print("  PASSED: both runs reconcile and never go cash-negative; vol-sizing's average "
          "position size never exceeds the baseline's.\n")


def test_vol_sizing_wiring_end_to_end():
    """The pure-formula test above validates vol_sizing_multiplier() in
    isolation; the universe used by the other integration test happens to
    have low enough portfolio volatility that the multiplier never binds
    below 1.0 anywhere (confirmed separately -- that is a legitimate
    property of that fixture, not evidence the wiring works). This test
    hand-builds a scenario that DOES drive trailing realized portfolio
    volatility far above the 20% target, then confirms, via a direct
    baseline-vs-candidate comparison on the IDENTICAL signal, that the
    exact same entry buys fewer shares under vol-sizing -- closing the
    loop on whether the engine's `daily_rows` -> vol_sizing_multiplier()
    -> budget wiring is actually correct, not just the formula alone.

    Ticker V enters immediately and is held open throughout (always
    green, TP unreachable) with daily closes alternating sharply
    (+20%/-16% days), which dominates the 2-position portfolio's own
    daily equity swings -- by week 5 there are 25 trading days of this,
    comfortably exceeding the 20-day lookback with annualized realized
    volatility far above 20%. Ticker E's entry condition fires fresh in
    week 5, once this elevated-volatility history already exists."""
    weekly_dates = pd.to_datetime(["2015-01-05", "2015-01-12", "2015-01-19",
                                    "2015-01-26", "2015-02-02", "2015-02-09"])
    daily_dates = pd.bdate_range("2015-01-05", periods=30)

    v_closes = [100.0 * (1.20 if k % 2 == 0 else 0.84) for k in range(30)]
    v_opens = [100.0] + v_closes[:-1]
    v_daily = pd.DataFrame({
        "Open": v_opens, "High": [max(o, c) * 1.01 for o, c in zip(v_opens, v_closes)],
        "Low": [min(o, c) * 0.99 for o, c in zip(v_opens, v_closes)], "Close": v_closes,
    }, index=daily_dates)
    v_weekly = pd.DataFrame({
        "Open": [100.0] * 6, "High": [106.0] * 6, "Low": [99.0] * 6, "Close": [105.0] * 6,
        "basis": [np.nan] * 6, "upperR": [100.0, 102.0, 102.0, 102.0, 102.0, 102.0], "lowerR": [np.nan] * 6,
        "green": [1.0] * 6, "lowerB": [np.nan] * 6, "pivot_conf": [np.nan] * 6,
        "prev_hh": [np.nan, 500.0, 500.0, 500.0, 500.0, 500.0],
    }, index=weekly_dates)

    e_placeholder = pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5},
                                  index=daily_dates[0:25])
    e_week5 = pd.DataFrame({"Open": [50.5, 51, 51.5, 52, 52.5], "High": [52, 52.5, 53, 53.5, 54],
                             "Low": [50, 50.5, 51, 51.5, 52], "Close": [51, 51.5, 52, 52.5, 53]},
                            index=daily_dates[25:30])
    e_daily = pd.concat([e_placeholder, e_week5])
    e_weekly = pd.DataFrame({
        "Open": [100] * 5 + [50.5], "High": [101] * 5 + [54], "Low": [99] * 5 + [50],
        "Close": [100.5] * 5 + [53], "basis": [np.nan] * 6, "upperR": [98] * 5 + [52], "lowerR": [np.nan] * 6,
        "green": [1.0] * 6, "lowerB": [np.nan] * 6, "pivot_conf": [np.nan] * 6, "prev_hh": [np.nan] * 6,
    }, index=weekly_dates)

    tickers_prepped = {"V": (v_weekly, v_daily), "E": (e_weekly, e_daily)}
    cand = run_corrected_portfolio(tickers_prepped, max_positions=2, use_vol_sizing=True)
    base = run_corrected_portfolio(tickers_prepped, max_positions=2, use_vol_sizing=False)
    _, _, _, cand_open, _ = cand
    _, _, _, base_open, _ = base

    cand_e = cand_open[cand_open["ticker"] == "E"]
    base_e = base_open[base_open["ticker"] == "E"]
    assert len(cand_e) == 1 and len(base_e) == 1, "FAILED: ticker E's entry didn't go through in one or both runs"
    assert base_e["exposure_multiplier"].iloc[0] == 1.0, "FAILED: baseline's E entry should carry multiplier 1.0"
    cand_mult = cand_e["exposure_multiplier"].iloc[0]
    assert cand_mult < 1.0, (
        f"FAILED: candidate's E entry multiplier is {cand_mult} -- expected a sub-1.0 multiplier given "
        f"the deliberately elevated trailing portfolio volatility from V's alternating swings")
    assert cand_e["shares"].iloc[0] < base_e["shares"].iloc[0], (
        f"FAILED: candidate bought {cand_e['shares'].iloc[0]} shares of E, not fewer than baseline's "
        f"{base_e['shares'].iloc[0]} -- the multiplier isn't actually shrinking the real share count")
    print(f"vol-sizing-wiring-end-to-end test: E's entry under deliberately elevated trailing portfolio "
          f"volatility, baseline vs candidate, checked")
    print(f"  PASSED: candidate applied multiplier {cand_mult:.4f} to E's entry, buying "
          f"{cand_e['shares'].iloc[0]} shares (${cand_e['cost_basis'].iloc[0]:,.2f}) vs baseline's "
          f"{base_e['shares'].iloc[0]} shares (${base_e['cost_basis'].iloc[0]:,.2f}) for the identical signal.\n")


# --------------------------------------------------------------------------
# REAL-DATA PIPELINE -- runs BOTH strategies through the same corrected
# engine on the same data, exports CSVs for each, prints a comparison.
# --------------------------------------------------------------------------
def run_comparison(tickers: list, output_tag: str = ""):
    """output_tag: appended to every output filename's "milestone_c"
    prefix (e.g. "_sp500" -> milestone_c_sp500_baseline_trades.csv), so a
    full-S&P-500 run never overwrites the default 170-name-universe
    run's CSVs -- both can be kept side by side."""
    tickers_prepped = {}
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
        tickers_prepped[tk] = (prepped, daily)
        print(f"[{i+1}/{len(tickers)}] {tk}: prepped ({len(prepped)} weekly bars)", file=sys.stderr)

    print(f"\n{len(tickers_prepped)}/{len(tickers)} tickers processed, {len(skipped_tickers)} skipped: "
          f"{skipped_tickers}", file=sys.stderr)

    print("\nRunning BASELINE (corrected engine, equal-weight sizing)...", file=sys.stderr)
    base_daily, base_trades, base_skipped, base_open, base_failures = run_corrected_portfolio(
        tickers_prepped, use_vol_sizing=False)
    base_recon = reconcile_ledger(START_CAPITAL, base_trades, base_open, base_daily)
    base_metrics = compute_metrics(base_daily, base_trades, base_open, START_CAPITAL)

    print("Running VOL-SIZING CANDIDATE (corrected engine, 20d-vol-targeted sizing)...", file=sys.stderr)
    cand_daily, cand_trades, cand_skipped, cand_open, cand_failures = run_corrected_portfolio(
        tickers_prepped, use_vol_sizing=True)
    cand_recon = reconcile_ledger(START_CAPITAL, cand_trades, cand_open, cand_daily)
    cand_metrics = compute_metrics(cand_daily, cand_trades, cand_open, START_CAPITAL)

    for label, prefix, daily_df, trades_df, skipped_df, open_df, failures_df, recon in [
        ("baseline", f"milestone_c{output_tag}_baseline", base_daily, base_trades, base_skipped, base_open, base_failures, base_recon),
        ("vol_sizing", f"milestone_c{output_tag}_vol_sizing", cand_daily, cand_trades, cand_skipped, cand_open, cand_failures, cand_recon),
    ]:
        trades_df.to_csv(f"{prefix}_trades.csv", index=False)
        daily_df.to_csv(f"{prefix}_daily_equity.csv")
        if len(skipped_df):
            skipped_df.to_csv(f"{prefix}_skipped_signals.csv", index=False)
        open_df.to_csv(f"{prefix}_open_positions.csv", index=False)
        if len(failures_df):
            failures_df.to_csv(f"{prefix}_verification_failures.csv", index=False)
        pd.DataFrame([recon]).to_csv(f"{prefix}_reconciliation.csv", index=False)
        print(f"\nSaved {label}: {prefix}_trades.csv ({len(trades_df)} rows), "
              f"{prefix}_daily_equity.csv ({len(daily_df)} rows), "
              f"{prefix}_open_positions.csv ({len(open_df)} rows), "
              f"{prefix}_reconciliation.csv", file=sys.stderr)

    all_keys = list(base_metrics.keys())
    comparison_rows = [{"metric": k, "baseline": base_metrics.get(k), "vol_sizing_candidate": cand_metrics.get(k)}
                        for k in all_keys]
    comparison_df = pd.DataFrame(comparison_rows)
    comparison_path = f"milestone_c{output_tag}_comparison.csv"
    comparison_df.to_csv(comparison_path, index=False)

    pd.set_option("display.width", 160)
    print(f"\n=== MILESTONE C{output_tag.upper()}: BASELINE (corrected engine) vs VOLATILITY-ADJUSTED SIZING CANDIDATE ===")
    print(comparison_df.to_string(index=False))
    print("\n=== RECONCILIATION -- baseline ===")
    print(pd.Series(base_recon).to_string())
    print("\n=== RECONCILIATION -- vol-sizing candidate ===")
    print(pd.Series(cand_recon).to_string())
    print(f"\nSaved: {comparison_path}")
    return base_metrics, cand_metrics, base_recon, cand_recon


# --------------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", type=str, default=None)
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--sp500", action="store_true",
                     help="Use the full S&P 500 (bt.SP500_TICKERS, ~503 names) instead of the "
                          "default S&P 100 + NASDAQ 100 combined universe (bt.UNIVERSE, 170 names). "
                          "NOTE: the 170-name default was chosen deliberately to match the universe "
                          "Peter himself says he tested the strategy on -- see the comment above "
                          "UNIVERSE in backtest_33fvb.py. A full-S&P-500 run answers a different "
                          "question (does this generalize beyond Peter's own universe?), not a "
                          "like-for-like rerun of the existing comparison. Output files get a "
                          "'_sp500' tag so they never overwrite the default-universe run's CSVs.")
    args = ap.parse_args()

    print("Running vol-sizing-multiplier-formula test...\n")
    test_vol_sizing_multiplier_formula()
    print("Running unfinished-position-retained-at-end test...\n")
    test_unfinished_position_retained_at_end()
    print("Running portfolio-skip-does-not-suppress-future-entry test...\n")
    test_portfolio_skip_does_not_suppress_future_entry()
    print("Running corrected-engine-cash-conservation test...\n")
    test_corrected_engine_cash_conservation()
    print("Running vol-sizing-shrinks-positions-vs-baseline test...\n")
    test_vol_sizing_shrinks_positions_vs_baseline()
    print("Running vol-sizing-wiring-end-to-end test...\n")
    test_vol_sizing_wiring_end_to_end()

    if args.synthetic:
        return

    if args.tickers:
        tickers = args.tickers.split(",")
        output_tag = ""
    elif args.sp500:
        tickers = list(bt.SP500_TICKERS)
        output_tag = "_sp500"
        print(f"\nUsing the full S&P 500 universe ({len(tickers)} tickers) -- "
              f"this is a DIFFERENT question from the default comparison (does the strategy "
              f"generalize beyond Peter's own validated S&P100+NASDAQ100 universe?), not a "
              f"rerun of it. Outputs will be tagged 'milestone_c_sp500_*'.\n", file=sys.stderr)
    else:
        tickers = list(bt.UNIVERSE)
        output_tag = ""

    run_comparison(tickers, output_tag=output_tag)


if __name__ == "__main__":
    main()
