import backtest_33fvb as bt

for tk in ["SPY", "SOFI", "PLTR", "WMT"]:
    df = bt.fetch(tk)
    p = bt.prep(df)
    trades = bt.run(p, band="High", exit_mode="NBarHigh")
    print(f"{tk}: {len(trades)} trades")
    for t in trades:
        print(f"   entry {t['entry_date'].date()} ret {100*t['ret']:+.1f}% entry_px={t['entry_px']:.2f} exit_px={t['exit_px']:.2f}")

print("\nFull universe aggregate:")
total, wins, rets = 0, 0, []
for tk in bt.UNIVERSE:
    try:
        df = bt.fetch(tk)
        if df is None or len(df) < bt.FVB_LEN*5:
            continue
        p = bt.prep(df)
        trades = bt.run(p, band="High", exit_mode="NBarHigh")
        for t in trades:
            total += 1
            if t["ret"] > 0: wins += 1
            rets.append(t["ret"])
    except Exception as e:
        print(f"{tk}: error {e}")
import numpy as np
r = np.array(rets)
w, l = r[r>0], r[r<=0]
pf = w.sum()/-l.sum() if l.sum()<0 else float('nan')
print(f"{total} trades, {100*wins/total:.1f}% win, {100*r.mean():+.2f}% expectancy, PF {pf:.2f}")
