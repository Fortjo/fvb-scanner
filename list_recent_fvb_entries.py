#!/usr/bin/env python3
"""
List every FVB entry signal (live config, backtest_33fvb.run) since a start date, across the
universe, with entry week and entry price -- INCLUDING trades still open today.
Used to test timing FVB entries with a daily indicator (PPS/RSI KILLER "Bottom" label).

bt.run only returns closed trades, so a sentinel bar is appended one week after the last bar
with an unreachable-high High: any still-open position "exits" there and is marked OPEN.
Print-only. Usage: python list_recent_fvb_entries.py 2025-10-01
"""
import sys
import pandas as pd
import backtest_33fvb as bt

start = pd.Timestamp(sys.argv[1] if len(sys.argv) > 1 else "2025-10-01")
rows = []
for tk in bt.UNIVERSE:
    try:
        df = bt.fetch(tk)
        if df is None or len(df) < bt.FVB_LEN * 5:
            continue
        p = bt.prep(df)
        last = p.iloc[[-1]].copy()
        sentinel_date = p.index[-1] + pd.Timedelta(days=7)
        last.index = [sentinel_date]
        last["High"] = 1e12
        p2 = pd.concat([p, last])
        for t in bt.run(p2, band="High", exit_mode="NBarHigh"):
            if t["entry_date"] >= start:
                i = p2.index.get_loc(t["entry_date"])
                epx = min(p2["Open"].iloc[i], p2["upperR"].iloc[i])
                status = "OPEN" if t["exit_date"] == sentinel_date else f"{t['reason']}@{t['exit_date'].date()}"
                rows.append((tk, t["entry_date"].date(), round(float(epx), 2), status))
    except Exception as e:
        print(f"{tk}: {e}", file=sys.stderr)
print(f"FVB entries since {start.date()}: {len(rows)}")
print("ENTRIES " + ";".join(f"{a},{b},{c},{d}" for a, b, c, d in rows))
