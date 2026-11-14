"""
Personal position tracking for the small-cap weekly scan email.
Mirrors my_positions.py exactly, just pointed at a separate CSV so the
small-cap book stays independent from the main FVB portfolio.

Expected small_cap_positions.csv format:
    ticker,entry_date,entry_price,position_size,signal_type,exit_date,exit_price

Open positions have exit_date/exit_price left blank. Once you close a
position, fill those two in — the row moves out of the "open" section
automatically and into the closed-trades summary instead.

Meant to be imported by send_small_cap_email.py, but runs standalone too:
    python3 small_cap_positions.py
"""
import numpy as np
import pandas as pd
import backtest_33fvb as bt
import my_positions  # reuse the richer section-builders (TP/risk/FVB state,
                      # alerts, stale-position flag) instead of duplicating
                      # them — see build_positions_section() etc. below.

POSITIONS_FILE = "small_cap_positions.csv"
STALE_MULTIPLIER = 1.5


def _load_all():
    try:
        df = pd.read_csv(POSITIONS_FILE, dtype=str)
    except FileNotFoundError:
        return None
    df = df[df["ticker"] != "FILL_IN"]
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


def load():
    df = load_open_positions()
    return df if df is not None else pd.DataFrame()


def get_open_tickers() -> set:
    df = load_open_positions()
    if df is None or df.empty:
        return set()
    return set(df["ticker"].str.upper())


def build_positions_section():
    """Same format as the main book's positions section (TP trigger, lower
    band, FVB state, stale/below-stop alerts) — reuses my_positions.py's
    logic directly, just pointed at small_cap_positions.csv."""
    return my_positions.build_positions_section(
        load_open_positions(), positions_file=POSITIONS_FILE,
        section_title="YOUR SMALL-CAP POSITIONS")


def build_closed_positions_section():
    return my_positions.build_closed_positions_section(
        load_closed_positions(), section_title="CLOSED SMALL-CAP TRADES")


def build_positions_section_html():
    return my_positions.build_positions_section_html(
        load_open_positions(), positions_file=POSITIONS_FILE,
        section_title="Your Small-Cap Positions")


def build_closed_positions_section_html():
    return my_positions.build_closed_positions_section_html(
        load_closed_positions(), section_title="Closed Small-Cap Trades")


if __name__ == "__main__":
    print(load())
    print(build_positions_section())
    print(build_closed_positions_section())
