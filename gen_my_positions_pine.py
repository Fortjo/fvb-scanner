#!/usr/bin/env python3
"""
Regenerates the auto-generated "My Positions" table inside
tht_33fvb_smz_volpro_combined.pine from my_positions.csv.

Why this exists (2026-11-25): the old design let Forty type an entry
price/date into the indicator's own input fields, per chart. In practice
TradingView carried those input values across his separately-saved charts
instead of keeping them per-instance (confirmed live: a BTCUSDC chart
showed the NVDA marker he'd just entered on the NVDA chart) -- so manual
per-chart inputs for this are not safe to use.

Fix: the position list is baked directly into the Pine SOURCE as a fixed
table, keyed by symbol (syminfo.ticker). Each chart only ever draws a
marker if ITS OWN symbol matches a row -- there is no input field to carry
between charts, so there is nothing to leak. The cost: whenever a position
opens or closes, this generator must be rerun and the .pine file re-pasted
into TradingView once (same as every other feature update so far).

Run from the fvb-scanner folder:
    python3 gen_my_positions_pine.py
Reads my_positions.csv (OPEN rows only -- blank exit_date), rewrites the
block between the AUTO-POSITIONS-BEGIN / AUTO-POSITIONS-END markers in
tht_33fvb_smz_volpro_combined.pine, in place. Safe to rerun any time.
"""
from __future__ import annotations
import csv
import sys
from datetime import datetime

CSV_PATH = "my_positions.csv"
PINE_PATH = "tht_33fvb_smz_volpro_combined.pine"
BEGIN = "    // AUTO-POSITIONS-BEGIN"
END = "    // AUTO-POSITIONS-END"


def load_open_positions():
    rows = []
    with open(CSV_PATH, newline="") as f:
        for r in csv.DictReader(f):
            if r.get("exit_date", "").strip():
                continue
            ticker = r["ticker"].strip().upper()
            entry_price = float(r["entry_price"])
            d = datetime.strptime(r["entry_date"].strip(), "%d.%m.%Y")
            stop = r.get("stop_price", "").strip()
            rows.append((ticker, entry_price, d, float(stop) if stop else None))
    return rows


def pine_lines(rows):
    out = []
    for ticker, price, d, stop in rows:
        ts = f'timestamp("{d.strftime("%Y-%m-%d")} 00:00")'
        stop_lit = f"{stop:.4f}" if stop is not None else "na"
        out.append(
            f'    array.push(__posTicker, "{ticker}"), array.push(__posEntry, {price:.4f}), '
            f'array.push(__posDate, {ts}), array.push(__posStop, {stop_lit})'
        )
    return out


def main():
    rows = load_open_positions()
    if not rows:
        print("No open positions found -- nothing to generate.", file=sys.stderr)
        return 1
    # The .pine file uses CRLF throughout; read with universal-newline
    # translation (-> \n) and write back with newline="\r\n" so the whole
    # file -- including the block we rewrite -- stays CRLF-consistent.
    with open(PINE_PATH, newline=None) as f:
        text = f.read()
    if BEGIN not in text or END not in text:
        print(f"Markers not found in {PINE_PATH} -- paste the v21 block in first.", file=sys.stderr)
        return 1
    before, rest = text.split(BEGIN, 1)
    _, after = rest.split(END, 1)
    new_block = BEGIN + "\n" + "\n".join(pine_lines(rows)) + "\n" + END
    with open(PINE_PATH, "w", newline="\r\n") as f:
        f.write(before + new_block + after)
    print(f"Wrote {len(rows)} open positions into {PINE_PATH}:")
    for ticker, price, d, stop in rows:
        stop_s = f", stop {stop}" if stop is not None else ""
        print(f"  {ticker}: entered {d:%Y-%m-%d} at {price}{stop_s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
