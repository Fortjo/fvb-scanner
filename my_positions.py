"""
Personal position tracking for the weekly FVB/SMZ scan email.

Expected my_positions.csv format:
    ticker,entry_date,entry_price,position_size,signal_type,exit_date,exit_price

Open positions have exit_date/exit_price left blank. Once you close a
position, fill those two in — the row moves out of the "open" section
automatically and into the closed-trades summary instead.

Meant to be imported by send_scan_email.py, but runs standalone too:
    python3 my_positions.py
"""
import numpy as np
import pandas as pd
import backtest_33fvb as bt

POSITIONS_FILE = "my_positions.csv"
STALE_MULTIPLIER = 1.5  # flag a position if it's held this many times longer
                        # than that ticker's own historical average hold


def _load_all():
    try:
        df = pd.read_csv(POSITIONS_FILE, dtype=str)
    except FileNotFoundError:
        return None
    df = df[df["ticker"] != "FILL_IN"]  # ignore un-edited template rows
    return df


def load_open_positions():
    df = _load_all()
    if df is None:
        return None
    open_df = df[df["exit_date"].isna() | (df["exit_date"].str.strip() == "")]
    return open_df


def load_closed_positions():
    df = _load_all()
    if df is None:
        return None
    closed_df = df[df["exit_date"].notna() & (df["exit_date"].str.strip() != "")]
    return closed_df


def get_open_tickers() -> set:
    """Just the set of tickers currently held — used by send_scan_email.py
    to filter them out of the fresh-signals list (no point flagging a
    'new' buy signal for something you're already holding)."""
    open_df = load_open_positions()
    if open_df is None or len(open_df) == 0:
        return set()
    return set(open_df["ticker"].str.strip().str.upper())


def compute_entry_tp_target(df: pd.DataFrame, entry_date, entry_price: float):
    """Mirrors backtest_33fvb.run()'s hybrid TP logic exactly, but for a
    MANUALLY recorded entry (my_positions.csv) rather than a simulated
    one: use the 200-week high as of entry if it's a real, non-stale
    value; otherwise fall back to the most recent confirmed swing pivot
    high before entry. Fixed once at entry — not recomputed every week,
    same principle as the live engine fix."""
    idx = df.index
    entry_ts = pd.Timestamp(entry_date)
    after = df.index[df.index >= entry_ts]
    if len(after) == 0:
        return np.nan
    entry_i = df.index.get_loc(after[0])

    prev_hh = df["prev_hh"].values[entry_i]
    piv = df["pivot_conf"].values

    hh_missing = np.isnan(prev_hh)
    hh_stale = (not hh_missing) and (prev_hh > entry_price * 2.5)

    if hh_missing or hh_stale:
        past_pivots = piv[:entry_i]
        valid = past_pivots[~np.isnan(past_pivots)]
        # [FIX] must be a real target above entry, not just the literal
        # most recent confirmed pivot — see matching fix in bt.run().
        above_entry = valid[valid > entry_price]
        if len(above_entry) > 0:
            return above_entry[-1]
        return prev_hh
    return prev_hh


def _historical_avg_hold(ticker: str) -> float:
    """Historical average hold time (weeks) for this ticker's own FVB
    trades, High band — used for the stale-position flag. Returns NaN
    if there's no trade history to compare against."""
    try:
        raw = bt.fetch(ticker)
    except Exception:
        return np.nan
    if raw is None:
        return np.nan
    df = bt.prep(raw)
    if len(df) < bt.NBAR_LEN + 20:
        return np.nan
    trades = bt.run(df, "High", "NBarHigh")
    if not trades:
        return np.nan
    return float(np.mean([t["hold"] for t in trades]))


def build_positions_section():
    open_df = load_open_positions()
    if open_df is None:
        return f"\n(No {POSITIONS_FILE} found — skipping your positions section. " \
               f"Upload it to the repo to enable this.)\n"
    if len(open_df) == 0:
        return "\n=== YOUR POSITIONS ===\n\nNo open positions currently tracked.\n"

    lines = ["\n=== YOUR POSITIONS ===\n"]
    alerts = []

    for _, row in open_df.iterrows():
        tk = row["ticker"].strip()
        try:
            entry_price = float(row["entry_price"])
            position_size = float(row["position_size"])
        except (ValueError, TypeError):
            lines.append(f"\n{tk} — could not read entry_price/position_size, check the file for typos")
            continue

        try:
            raw = bt.fetch(tk)
        except Exception:
            lines.append(f"\n{tk} — fetch error, skipping this week")
            continue
        if raw is None:
            lines.append(f"\n{tk} — no data available, skipping this week")
            continue

        df = bt.prep(raw)
        if len(df) < bt.NBAR_LEN + 20:
            lines.append(f"\n{tk} — insufficient history, skipping this week")
            continue

        current_price = df["Close"].values[-1]
        pct_pl = 100 * (current_price / entry_price - 1)
        dollar_pl = position_size * (current_price / entry_price - 1)

        # [TP FIX] use the FIXED entry-time target (hybrid: 200wk high,
        # or nearest confirmed swing high if that's missing/stale) —
        # matches bt.run()'s live logic, instead of the raw, possibly
        # stale prev_hh recomputed fresh every week.
        tp_target = compute_entry_tp_target(df, row["entry_date"], entry_price)
        lowerB = df["lowerB"].values[-1]
        fvb_state = "GREEN" if df["green"].values[-1] else "RED"

        pct_to_tp = 100 * (tp_target / current_price - 1) if not np.isnan(tp_target) else np.nan
        pct_to_risk = 100 * (1 - lowerB / current_price) if not np.isnan(lowerB) else np.nan

        weeks_held = None
        try:
            entry_dt = pd.Timestamp(row["entry_date"])
            weeks_held = int((df.index[-1] - entry_dt).days / 7)
        except Exception:
            pass

        below_stop = (not np.isnan(pct_to_risk)) and pct_to_risk <= 0
        if fvb_state == "RED":
            alerts.append(f"{tk}: FVB state has flipped RED — stop condition likely triggered, check chart")
        elif below_stop:
            alerts.append(f"{tk}: trading below the stop level on a closing basis, state hasn't flipped yet — check chart")

        # [STALE POSITION FLAG] compare current hold time against this
        # ticker's own historical average hold (High band, same config
        # as the live engine). Only a heads-up, not a signal — some
        # perfectly good trades run long.
        stale_note = ""
        if weeks_held is not None:
            avg_hold = _historical_avg_hold(tk)
            if not np.isnan(avg_hold) and avg_hold > 0 and weeks_held > STALE_MULTIPLIER * avg_hold:
                stale_note = f"  ** Held {weeks_held}wks vs this ticker's own {avg_hold:.0f}wk average — running longer than usual, worth a look **"
                alerts.append(f"{tk}: held {weeks_held}wks, well beyond its own historical average of {avg_hold:.0f}wks")

        lines.append(f"\n{tk} — {row.get('signal_type', 'FVB')} — LONG since {row['entry_date']} "
                     f"({weeks_held if weeks_held is not None else '?'} wks)")
        lines.append(f"  Current ${current_price:.2f}, entry was ${entry_price:.2f}, "
                      f"P&L {pct_pl:+.1f}% (${dollar_pl:+.2f})")
        tp_str = f"{pct_to_tp:.1f}%" if not np.isnan(pct_to_tp) else "n/a"
        risk_str = f"{pct_to_risk:.1f}%" if not np.isnan(pct_to_risk) else "n/a"
        lines.append(f"  TP trigger: {tp_str} away | Lower band: {risk_str} below | FVB state: {fvb_state}")
        if stale_note:
            lines.append(stale_note)

    if alerts:
        alert_block = "\n** ALERTS THIS WEEK **\n" + "\n".join(f"  - {a}" for a in alerts) + "\n"
        lines = [alert_block] + lines

    return "\n".join(lines)


def build_closed_positions_section():
    """Summarize realized P&L for every closed trade, plus running totals."""
    closed_df = load_closed_positions()
    if closed_df is None or len(closed_df) == 0:
        return "\n=== CLOSED TRADES ===\n\nNo closed trades yet.\n"

    lines = ["\n=== CLOSED TRADES ===\n"]
    total_dollar_pl = 0.0
    wins = 0
    losses = 0

    for _, row in closed_df.iterrows():
        tk = row["ticker"].strip()
        try:
            entry_price = float(row["entry_price"])
            exit_price = float(row["exit_price"])
            position_size = float(row["position_size"])
        except (ValueError, TypeError):
            lines.append(f"\n{tk} — could not read entry/exit price or size, check the file for typos")
            continue

        pct_pl = 100 * (exit_price / entry_price - 1)
        dollar_pl = position_size * (exit_price / entry_price - 1)
        total_dollar_pl += dollar_pl
        if dollar_pl >= 0:
            wins += 1
        else:
            losses += 1

        held = "?"
        try:
            entry_dt = pd.Timestamp(row["entry_date"])
            exit_dt = pd.Timestamp(row["exit_date"])
            held = int((exit_dt - entry_dt).days / 7)
        except Exception:
            pass

        lines.append(
            f"\n{tk} — {row.get('signal_type', 'FVB')} — "
            f"{row['entry_date']} \u2192 {row['exit_date']} ({held} wks)"
        )
        lines.append(
            f"  Entry ${entry_price:.2f} \u2192 Exit ${exit_price:.2f}, "
            f"P&L {pct_pl:+.1f}% (${dollar_pl:+.2f})"
        )

    total_trades = wins + losses
    win_rate = f"{100 * wins / total_trades:.0f}%" if total_trades else "n/a"
    lines.append(
        f"\n--- Totals: {total_trades} closed trades, {wins}W/{losses}L "
        f"({win_rate} win rate), net P&L ${total_dollar_pl:+.2f} ---\n"
    )

    return "\n".join(lines)


if __name__ == "__main__":
    print(build_positions_section())
    print(build_closed_positions_section())
