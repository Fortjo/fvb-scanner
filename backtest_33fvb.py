#!/usr/bin/env python3
"""
33FVB Strategy Reconstruction — Python backtest (yfinance)
===========================================================
Tests the testable core of the "THT 33FVB Strategy / SMZ" reconstruction:
    Entry bands:  High / Mid / Low  (pullback to band while FVB is GREEN)
    Exits:        NBarHigh (new 200-bar high)  /  SwingHigh (first confirmed
                  5/5 pivot high after entry)
    Stop:         CALIBRATED — close below the lower FVB band (see below)
    Costs:        0.1% round trip per trade

Mirrors the Pine reconstruction's CALIBRATED assumptions (tuned against
Peter's MSFT dashboard: 13 trades, 84.6% win, TP/Stop 11/2, avg loss -38.5%):
    [A1] halfwidth = basis * stdev(monthly log-returns, 33) * BAND_MULT (3.0)
         Percentage-based, not raw-dollar stdev — avoids the "snake" band
         behavior raw-dollar stdev produced on the Pine side.
    [A2] band depth below basis: High=0.15 (calibrated), Mid=0.50, Low=1.00
    [A3] ENTRY gate (green) = monthly basis rising (option: close > basis)
    [A3b] STOP is DECOUPLED from the entry gate — calibration showed a
         basis-slope-flip stop fired far too often (5 stops vs Peter's 2).
         Default is now close < lower band (best-scoring in calibration).
    [A5] limit-style entry fill = min(open, band)
    No lookahead: monthly basis/state values are shifted one full month before
    being mapped onto weekly bars (stricter than TradingView's intramonth
    behavior — results here should be the conservative floor).

Usage:
    pip install yfinance pandas numpy
    python backtest_33fvb.py                 # full grid, default universe
    python backtest_33fvb.py --tickers MSFT,AAPL,NVDA
    python backtest_33fvb.py --split 2018-01-01   # train/test OOS split
    python backtest_33fvb.py --synthetic     # no-network smoke test

Outputs: results_per_ticker.csv, results_summary.csv (printed too).
Replace UNIVERSE below with your validated 82-ticker list for a
like-for-like comparison with your BX+FVB test.
"""
import argparse
import sys
import numpy as np
import pandas as pd

# --------------------------------------------------------------------------
# CONFIG
# --------------------------------------------------------------------------
FVB_LEN     = 33          # monthly SMA / stdev length
BAND_MULT   = 2.0         # [A1] halfwidth = basis * %vol * this
# [A2 CORRECTED] Entry bands are SIGNED positions relative to basis:
# +1 = upper band ("shallow fill"), 0 = basis, -1 = lower band ("deep discount").
# The original reconstruction wrongly placed all three below the basis; a
# bar-by-bar MSFT diagnostic (1989/1993/2013/2015 pullbacks) proved real
# entries touch the UPPER band from above.
BAND_FRACS  = {"High": 1.0, "Mid": 0.0, "Low": -1.0}
NBAR_LEN    = 200         # weekly bars for "new N-bar high" exit
PIV_L, PIV_R = 5, 5       # swing pivot detection (weekly bars)
RT_COST     = 0.001       # 0.1% round trip
STATE_MODE  = "real_cross"  # [VALIDATED] the real algorithm — collapsed our ORCL
                          # flip count from 12 down to 1, matching the actual
                          # indicator almost exactly (Nov 2004 end date was exact).
                          # options: "real_cross" (recommended), "slope" (old, wrong), "close_vs_basis"
STOP_DEF    = "basis_slope"  # [UPDATED] this stop just checks "not green" — and green
                          # now comes from the REAL algorithm (STATE_MODE="real_cross"),
                          # not a raw slope. The name is kept for compatibility with
                          # compare_stops.py etc., but it now means "FVB state flips red,
                          # per the real quantifytools Cross-mode definition."
                          # options: "basis_slope", "close_below_basis", "close_below_lowerband"
START       = "1970-01-01"  # was 2000 — missed 14 years of ORCL's actual 1986+ history vs
                            # what a TradingView chart with full history loaded would show

# Your validated 82-ticker universe (momentum + blue-chip), from the BX+FVB testing.
MOMENTUM_TICKERS = [
    "ENPH", "LUNR", "FCX", "DOCN", "BAND", "HIMS", "INOD", "IVZ", "KALU",
    "NUE", "NVTS", "SANM", "STLD", "WS", "GLXY", "ADI", "CLS", "AFRM",
    "YOU", "AUR", "DAN", "NEOG",
    "RKLB", "ASTS", "JOBY", "ACHR",
    "COIN", "MARA", "RIOT", "CLSK", "MSTR", "SOFI", "UPST",
    "PLTR", "SMCI", "IONQ", "AI", "SOUN", "PATH", "RBLX",
    "CRSP", "NTLA", "BEAM",
    "RIVN", "LCID", "NIO", "CVNA",
    "OKLO", "SMR", "VST", "CEG",
    "DKNG", "ROKU", "PLUG",
]
BLUECHIP_TICKERS = [
    "AAPL", "MSFT", "JNJ", "KO", "PG", "WMT", "JPM", "HD",
    "V", "MA", "UNH", "XOM", "CVX", "MRK", "PEP", "COST",
    "MCD", "DIS", "VZ", "T", "IBM", "CAT", "BA", "GE",
    "MMM", "ABT", "TXN", "HON",
]
SP500_TICKERS = [
    "MMM", "AOS", "ABT", "ABBV", "ACN", "ADBE", "AMD", "AES",
    "AFL", "A", "APD", "ABNB", "AKAM", "ALB", "ARE", "ALGN",
    "ALLE", "LNT", "ALL", "GOOGL", "GOOG", "MO", "AMZN", "AMCR",
    "AEE", "AEP", "AXP", "AIG", "AMT", "AWK", "AMP", "AME",
    "AMGN", "APH", "ADI", "AON", "APA", "APO", "AAPL", "AMAT",
    "APP", "APTV", "ACGL", "ADM", "ARES", "ANET", "AJG", "AIZ",
    "T", "ATO", "ADSK", "ADP", "AZO", "AVB", "AVY", "AXON",
    "BKR", "BALL", "BAC", "BAX", "BDX", "BRK-B", "BBY", "TECH",
    "BIIB", "BLK", "BX", "XYZ", "BNY", "BA", "BKNG", "BSX",
    "BMY", "AVGO", "BR", "BRO", "BF-B", "BLDR", "BG", "BXP",
    "CHRW", "CDNS", "CPT", "COF", "CAH", "CCL", "CARR", "CVNA",
    "CASY", "CAT", "CBOE", "CBRE", "CDW", "COR", "CNC", "CNP",
    "CF", "CRL", "SCHW", "CHTR", "CVX", "CMG", "CB", "CHD",
    "CIEN", "CI", "CINF", "CTAS", "CSCO", "C", "CFG", "CLX",
    "CME", "CMS", "KO", "CTSH", "COHR", "COIN", "CL", "CMCSA",
    "FIX", "COP", "ED", "STZ", "CEG", "COO", "CPRT", "GLW",
    "CPAY", "CTVA", "CSGP", "COST", "CRH", "CRWD", "CCI", "CSX",
    "CMI", "CVS", "DHR", "DRI", "DDOG", "DVA", "DECK", "DE",
    "DELL", "DAL", "DVN", "DXCM", "FANG", "DLR", "DG", "DLTR",
    "D", "DPZ", "DASH", "DOV", "DOW", "DHI", "DTE", "DUK",
    "DD", "ETN", "EBAY", "ECHO", "ECL", "EIX", "EW", "EA",
    "ELV", "EME", "EMR", "ETR", "EOG", "EQT", "EFX", "EQIX",
    "EQR", "ERIE", "ESS", "EL", "EG", "EVRG", "ES", "EXC",
    "EXE", "EXPE", "EXPD", "EXR", "XOM", "FFIV", "FDS", "FICO",
    "FAST", "FRT", "FDX", "FDXF", "FIS", "FITB", "FSLR", "FE",
    "FISV", "FLEX", "F", "FTNT", "FTV", "FOXA", "FOX", "BEN",
    "FCX", "GRMN", "IT", "GE", "GEHC", "GEV", "GEN", "GNRC",
    "GD", "GIS", "GM", "GPC", "GILD", "GPN", "GL", "GDDY",
    "GS", "HAL", "HIG", "HAS", "HCA", "DOC", "HSIC", "HSY",
    "HPE", "HLT", "HD", "HONA", "HON", "HRL", "HST", "HWM",
    "HPQ", "HUBB", "HUM", "HBAN", "HII", "IBM", "IEX", "IDXX",
    "ITW", "INCY", "IR", "PODD", "INTC", "IBKR", "ICE", "IFF",
    "IP", "INTU", "ISRG", "IVZ", "INVH", "IQV", "IRM", "JBHT",
    "JBL", "JKHY", "J", "JNJ", "JCI", "JPM", "KVUE", "KDP",
    "KEY", "KEYS", "KMB", "KIM", "KMI", "KKR", "KLAC", "KHC",
    "KR", "LHX", "LH", "LRCX", "LVS", "LDOS", "LEN", "LII",
    "LLY", "LIN", "LYV", "LMT", "L", "LOW", "LULU", "LITE",
    "LYB", "MTB", "MPC", "MAR", "MRSH", "MLM", "MRVL", "MAS",
    "MA", "MKC", "MCD", "MCK", "MDT", "MRK", "META", "MET",
    "MTD", "MGM", "MCHP", "MU", "MSFT", "MAA", "MRNA", "TAP",
    "MDLZ", "MPWR", "MNST", "MCO", "MS", "MOS", "MSI", "MSCI",
    "NDAQ", "NTAP", "NFLX", "NEM", "NWSA", "NWS", "NEE", "NKE",
    "NI", "NDSN", "NSC", "NTRS", "NOC", "NCLH", "NRG", "NUE",
    "NVDA", "NVR", "NXPI", "ORLY", "OXY", "ODFL", "OMC", "ON",
    "OKE", "ORCL", "OTIS", "PCAR", "PKG", "PLTR", "PANW", "PSKY",
    "PH", "PAYX", "PYPL", "PNR", "PEP", "PFE", "PCG", "PM",
    "PSX", "PNW", "PNC", "PPG", "PPL", "PFG", "PG", "PGR",
    "PLD", "PRU", "PEG", "PTC", "PSA", "PHM", "PWR", "QCOM",
    "DGX", "Q", "RL", "RJF", "RTX", "O", "REG", "REGN",
    "RF", "RSG", "RMD", "RVTY", "HOOD", "ROK", "ROL", "ROP",
    "ROST", "RCL", "SPGI", "CRM", "SNDK", "SBAC", "SLB", "STX",
    "SRE", "NOW", "SHW", "SPG", "SWKS", "SJM", "SW", "SNA",
    "SOLV", "SO", "LUV", "SWK", "SBUX", "STT", "STLD", "STE",
    "SYK", "SMCI", "SYF", "SNPS", "SYY", "TMUS", "TROW", "TTWO",
    "TPR", "TRGP", "TGT", "TEL", "TDY", "TER", "TSLA", "TXN",
    "TPL", "TXT", "TMO", "TJX", "TKO", "TTD", "TSCO", "TT",
    "TDG", "TRV", "TRMB", "TFC", "TYL", "TSN", "USB", "UBER",
    "UDR", "ULTA", "UNP", "UAL", "UPS", "URI", "UNH", "UHS",
    "VLO", "VEEV", "VTR", "VLTO", "VRSN", "VRSK", "VZ", "VRTX",
    "VRT", "VTRS", "VICI", "V", "VST", "VMC", "WRB", "GWW",
    "WAB", "WMT", "DIS", "WBD", "WM", "WAT", "WEC", "WFC",
    "WELL", "WST", "WDC", "WY", "WSM", "WMB", "WTW", "WDAY",
    "WYNN", "XEL", "XYL", "YUM", "ZBRA", "ZBH", "ZTS",
]

SP100_NDX100_TICKERS = [
    "AAPL", "ABBV", "ABNB", "ABT", "ACN", "ADBE", "ADI", "ADP",
    "ADSK", "AEP", "AIG", "ALNY", "AMAT", "AMD", "AMGN", "AMT",
    "AMZN", "APP", "ARM", "ASML", "AVGO", "AXON", "AXP", "BA",
    "BAC", "BK", "BKNG", "BKR", "BLK", "BMY", "BRK-B", "C",
    "CAT", "CCEP", "CDNS", "CEG", "CHTR", "CL", "CMCSA", "COF",
    "COP", "COST", "CPRT", "CRM", "CRWD", "CSCO", "CSGP", "CSX",
    "CTAS", "CTSH", "CVS", "CVX", "DASH", "DDOG", "DE", "DHR",
    "DIS", "DUK", "DXCM", "EA", "EMR", "EXC", "FANG", "FAST",
    "FDX", "FER", "FTNT", "GD", "GE", "GEHC", "GILD", "GM",
    "GOOG", "GOOGL", "GS", "HD", "HON", "IBM", "IDXX", "INSM",
    "INTC", "INTU", "ISRG", "JNJ", "JPM", "KDP", "KHC", "KLAC",
    "KO", "LIN", "LLY", "LMT", "LOW", "LRCX", "MA", "MAR",
    "MCD", "MCHP", "MDLZ", "MDT", "MELI", "MET", "META", "MMM",
    "MNST", "MO", "MPWR", "MRK", "MRVL", "MS", "MSFT", "MSTR",
    "MU", "NEE", "NFLX", "NKE", "NOW", "NVDA", "NXPI", "ODFL",
    "ORCL", "ORLY", "PANW", "PAYX", "PCAR", "PDD", "PEP", "PFE",
    "PG", "PLTR", "PM", "PYPL", "QCOM", "REGN", "ROP", "ROST",
    "RTX", "SBUX", "SCHW", "SHOP", "SNPS", "SO", "SPG", "STX",
    "T", "TEAM", "TGT", "TMO", "TMUS", "TRI", "TSLA", "TTWO",
    "TXN", "UBER", "UNH", "UNP", "UPS", "USB", "V", "VRSK",
    "VRTX", "VZ", "WBD", "WDAY", "WDC", "WFC", "WMT", "XEL",
    "XOM", "ZS",
]

# [MATCHING PETER'S ACTUAL UNIVERSE] Peter's own video: strategy is tested
# on "only the S&P 100, only the NASDAQ 100 combined... only 200 names."
# We were scanning the full S&P 500 (503 names) — hundreds of smaller,
# more volatile stocks the strategy was never validated on, which is very
# likely part of why so many "Weak"/"Mixed" ratings showed up. This is
# now the default, deduplicated S&P 100 + NASDAQ-100 combined (170 names,
# some overlap between the two indices).
UNIVERSE = SP100_NDX100_TICKERS
# Other options, comment/uncomment to switch:
# UNIVERSE = SP500_TICKERS                       # full S&P 500 (503 names, slower)
# UNIVERSE = MOMENTUM_TICKERS + BLUECHIP_TICKERS  # original small curated list (~82)

# --------------------------------------------------------------------------
# DATA
# --------------------------------------------------------------------------
def fetch(ticker: str) -> pd.DataFrame | None:
    import yfinance as yf
    df = yf.download(ticker, start=START, interval="1wk",
                     auto_adjust=True, progress=False)
    if df is None or len(df) < FVB_LEN * 5:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df[["Open", "High", "Low", "Close"]].dropna()

def synthetic(ticker: str, n=1300, seed=None) -> pd.DataFrame:
    rng = np.random.default_rng(abs(hash(ticker)) % 2**32 if seed is None else seed)
    rets = rng.normal(0.0018, 0.035, n)
    close = 50 * np.exp(np.cumsum(rets))
    open_ = close * (1 + rng.normal(0, 0.01, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.015, n)))
    low  = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.015, n)))
    idx = pd.date_range("2000-01-07", periods=n, freq="W-FRI")
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close}, index=idx)
from collections import deque

def _compute_real_fvb(m: pd.DataFrame, length: int):
    """The REAL Fair Value Bands algorithm (quantifytools' open-source script,
    Cross mode, boost=1.0), validated against ORCL: collapsed our flip count
    from 12 down to essentially 1, matching the real indicator's one major
    flip (Aug 2001-ish -> Nov 2004, exact match on the end date) that you
    confirmed by applying the actual script to the chart. Two parts, both
    load-bearing, and BOTH now used (state AND the band itself):
      1. Threshold band = MEDIAN of historical high/low deviation ratios from
         the basis (only counting bars whose range straddles the basis),
         tracked over up to 1000 bars — not a stdev multiple like our old
         percentage-volatility guess.
      2. Trend state = STICKY: red only when this bar's HIGH closes below
         the lower band, green only when this bar's LOW closes above the
         upper band. Otherwise holds its previous value.
    Returns (green, upper_band, lower_band) — all pd.Series aligned to m's index.
    """
    ohlc4 = (m["Open"] + m["High"] + m["Low"] + m["Close"]) / 4
    basis = ohlc4.rolling(length).mean()
    low_spread = m["Low"] / basis
    high_spread = m["High"] / basis
    straddle = (m["Low"] < basis) & (m["High"] > basis)

    n = len(m)
    dev_up = np.where(straddle, high_spread, np.nan)
    dev_down = np.where(straddle, low_spread, np.nan)

    median_up = np.full(n, np.nan)
    median_down = np.full(n, np.nan)
    up_win, down_win = deque(maxlen=1000), deque(maxlen=1000)
    for i in range(n):
        up_win.append(dev_up[i])
        down_win.append(dev_down[i])
        vu = [x for x in up_win if not np.isnan(x)]
        vd = [x for x in down_win if not np.isnan(x)]
        if vu:
            median_up[i] = np.median(vu)
        if vd:
            median_down[i] = np.median(vd)

    upper_band = basis.values * median_up
    lower_band = basis.values * median_down

    dir_switch = np.zeros(n, dtype=int)
    state = 0
    hi, lo = m["High"].values, m["Low"].values
    for i in range(n):
        if not np.isnan(lower_band[i]) and hi[i] < lower_band[i]:
            state = -1
        elif not np.isnan(upper_band[i]) and lo[i] > upper_band[i]:
            state = 1
        dir_switch[i] = state

    green = pd.Series(dir_switch == 1, index=m.index)
    upper = pd.Series(upper_band, index=m.index)
    lower = pd.Series(lower_band, index=m.index)
    return green, upper, lower

# --------------------------------------------------------------------------
# INDICATOR PREP  (monthly FVB -> weekly bars, shifted 1 month, no lookahead)
# --------------------------------------------------------------------------
def prep(df: pd.DataFrame) -> pd.DataFrame:
    m = df.resample("ME").agg({"Open": "first", "High": "max",
                               "Low": "min", "Close": "last"}).dropna()
    ohlc4 = (m["Open"] + m["High"] + m["Low"] + m["Close"]) / 4
    basis = ohlc4.rolling(FVB_LEN).mean()
    if STATE_MODE == "real_cross":
        # [FULL TRANSPLANT] use the REAL median-deviation band for entries
        # too, not just the state — this was the other half of the
        # reconstruction still using our own guessed percentage-volatility
        # formula even after the state was fixed, and the likely source of
        # the remaining gap to Peter's numbers (14 trades/+2328% vs his
        # 17/+5085% — stop count already matched exactly at 1/1).
        green, upperR, lowerR = _compute_real_fvb(m, FVB_LEN)
        monthly = pd.DataFrame({"basis": basis, "upperR": upperR, "lowerR": lowerR,
                                "green": green}).shift(1)
        out = df.copy()
        mm = monthly.reindex(df.index, method="ffill")
        out[["basis", "upperR", "lowerR", "green"]] = mm
        out["halfw"]  = out["upperR"] - out["basis"]   # kept for any code expecting it
        out["lowerB"] = out["lowerR"]
    else:
        # [A1 CALIBRATED] percentage volatility (log-return stdev), not raw-dollar
        # stdev of price — raw-dollar stdev scaled with price level and produced
        # a jagged, price-disconnected band on the Pine side ("snake" bug).
        volpct = np.log(ohlc4 / ohlc4.shift(1)).rolling(FVB_LEN).std(ddof=0)
        halfw  = basis * volpct * BAND_MULT
        green = (basis > basis.shift(1)) if STATE_MODE == "slope" else (m["Close"] > basis)
        monthly = pd.DataFrame({"basis": basis, "halfw": halfw,
                                "green": green}).shift(1)   # use last COMPLETED month
        out = df.copy()
        mm = monthly.reindex(df.index, method="ffill")
        out[["basis", "halfw", "green"]] = mm
        out["lowerB"] = out["basis"] - out["halfw"]
    # confirmed swing pivot highs (known only PIV_R bars after the extreme)
    h = df["High"].values
    n = len(h)
    conf = np.full(n, np.nan)
    for i in range(PIV_L, n - PIV_R):
        w = h[i - PIV_L: i + PIV_R + 1]
        if h[i] == w.max() and (w == h[i]).sum() == 1:
            conf[i + PIV_R] = h[i]        # value known at confirmation bar
    out["pivot_conf"] = conf
    out["prev_hh"] = df["High"].rolling(NBAR_LEN).max().shift(1)
    return out.dropna(subset=["basis"])

# --------------------------------------------------------------------------
# BACKTEST one ticker / one config
# --------------------------------------------------------------------------
def run(df: pd.DataFrame, band: str, exit_mode: str) -> list[dict]:
    frac = BAND_FRACS[band]
    trades = []
    in_pos, entry_px, entry_i = False, np.nan, -1
    o, hgh, lw, cl = (df[c].values for c in ["Open", "High", "Low", "Close"])
    basis, halfw, green = df["basis"].values, df["halfw"].values, df["green"].values
    lowerB = df["lowerB"].values
    has_real_bands = "upperR" in df.columns
    upperR = df["upperR"].values if has_real_bands else None
    piv, prev_hh = df["pivot_conf"].values, df["prev_hh"].values
    idx = df.index
    for i in range(len(df)):
        if has_real_bands:
            # [FULL TRANSPLANT] real, asymmetric bands: High = real upper
            # band, Low = real lower band, Mid = basis itself — not a
            # symmetric fraction of a guessed half-width.
            band_px = upperR[i] if band == "High" else (lowerB[i] if band == "Low" else basis[i])
        else:
            band_px = basis[i] + frac * halfw[i]
        if in_pos and i > entry_i:
            exit_px, reason = None, None
            if exit_mode == "NBarHigh":
                if not np.isnan(prev_hh[i]) and hgh[i] >= prev_hh[i]:
                    exit_px, reason = max(o[i], prev_hh[i]), "TP"
            else:  # SwingHigh: first pivot confirmed after entry [A4]
                if not np.isnan(piv[i]) and (i - PIV_R) > entry_i:
                    exit_px, reason = cl[i], "TP"
            if exit_px is None:
                # [A3b CALIBRATED] stop decoupled from entry gate
                if STOP_DEF == "basis_slope":
                    stopped = not green[i]
                elif STOP_DEF == "close_below_basis":
                    stopped = cl[i] < basis[i]
                else:
                    stopped = cl[i] < lowerB[i]
                if stopped:
                    exit_px, reason = cl[i], "STOP"
            if exit_px is not None:
                ret = exit_px / entry_px - 1 - RT_COST
                trades.append({"entry_date": idx[entry_i], "exit_date": idx[i],
                               "ret": ret, "hold": i - entry_i, "reason": reason})
                in_pos = False
                continue
        # [BUG FIX] require prior close was already above the prior band —
        # otherwise "low <= band_px" is trivially true whenever price simply
        # hasn't risen up to the band yet (e.g. early in a stock's history),
        # producing one fluke trade that rides an entire uptrend from near
        # the historical floor. See matching fix in the Pine file.
        if i > 0:
            if has_real_bands:
                prior_band_px = upperR[i - 1] if band == "High" else (lowerB[i - 1] if band == "Low" else basis[i - 1])
            else:
                prior_band_px = basis[i - 1] + frac * halfw[i - 1]
        else:
            prior_band_px = np.nan
        if (not in_pos) and green[i] == True and not np.isnan(band_px) \
                and lw[i] <= band_px and i > 0 and not np.isnan(prior_band_px) \
                and cl[i - 1] > prior_band_px:                     # noqa: E712
            entry_px = min(o[i], band_px)                       # [A5]
            entry_i, in_pos = i, True
    return trades

# --------------------------------------------------------------------------
# STATS
# --------------------------------------------------------------------------
def stats(trades: list[dict]) -> dict:
    if not trades:
        return dict(trades=0, win_rate=np.nan, pf=np.nan, avg_win=np.nan,
                    avg_loss=np.nan, expectancy=np.nan, total_ret=np.nan,
                    max_dd=np.nan, avg_hold=np.nan, tp=0, stop=0)
    r = np.array([t["ret"] for t in trades])
    eq = np.cumprod(1 + r)
    peak = np.maximum.accumulate(eq)
    wins, losses = r[r > 0], r[r <= 0]
    return dict(
        trades=len(r),
        win_rate=100 * len(wins) / len(r),
        pf=wins.sum() / -losses.sum() if losses.sum() < 0 else np.inf,
        avg_win=100 * wins.mean() if len(wins) else np.nan,
        avg_loss=100 * losses.mean() if len(losses) else np.nan,
        expectancy=100 * r.mean(),
        total_ret=100 * (eq[-1] - 1),
        max_dd=100 * (eq / peak - 1).min(),
        avg_hold=np.mean([t["hold"] for t in trades]),
        tp=sum(t["reason"] == "TP" for t in trades),
        stop=sum(t["reason"] == "STOP" for t in trades),
    )

# --------------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", type=str, default=None)
    ap.add_argument("--split", type=str, default="2023-01-01",
                    help="date; report train (<) and test (>=) separately. "
                         "Default matches your prior BX+FVB validation split. "
                         "Pass --split none to disable.")
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args()

    tickers = args.tickers.split(",") if args.tickers else UNIVERSE
    rows = []
    for tk in tickers:
        try:
            raw = synthetic(tk) if args.synthetic else fetch(tk)
        except Exception as e:
            print(f"{tk}: fetch error {e}", file=sys.stderr)
            continue
        if raw is None:
            print(f"{tk}: insufficient data", file=sys.stderr)
            continue
        df = prep(raw)
        if len(df) < NBAR_LEN + 20:
            print(f"{tk}: too short after warmup", file=sys.stderr)
            continue
        for band in BAND_FRACS:
            for ex in ["NBarHigh", "SwingHigh"]:
                trs = run(df, band, ex)
                segs = [("all", trs)]
                if args.split and args.split.lower() != "none":
                    sd = pd.Timestamp(args.split)
                    segs = [("train", [t for t in trs if t["exit_date"] < sd]),
                            ("test",  [t for t in trs if t["exit_date"] >= sd])]
                for seg, tt in segs:
                    rows.append({"ticker": tk, "band": band, "exit": ex,
                                 "segment": seg, **stats(tt)})
        print(f"{tk}: done", file=sys.stderr)

    per = pd.DataFrame(rows)
    per.to_csv("results_per_ticker.csv", index=False)

    agg = (per.groupby(["band", "exit", "segment"])
              .agg(tickers=("ticker", "nunique"),
                   trades=("trades", "sum"),
                   med_win_rate=("win_rate", "median"),
                   med_pf=("pf", "median"),
                   med_expectancy=("expectancy", "median"),
                   med_total_ret=("total_ret", "median"),
                   med_max_dd=("max_dd", "median"),
                   med_hold=("avg_hold", "median"))
              .round(2).reset_index())
    agg.to_csv("results_summary.csv", index=False)
    pd.set_option("display.width", 160)
    print("\n=== SUMMARY (medians across tickers) ===")
    print(agg.to_string(index=False))
    print("\nSaved: results_per_ticker.csv, results_summary.csv")

    # Plain-English readout, focused on the out-of-sample ("test") segment,
    # which is the only one that tells you anything about future performance.
    test_rows = agg[agg["segment"] == "test"] if "test" in agg["segment"].values else agg[agg["segment"] == "all"]
    if len(test_rows):
        print("\n=== PLAIN-ENGLISH READOUT (out-of-sample only) ===")
        best = test_rows.sort_values("med_expectancy", ascending=False).iloc[0]
        print(f"Best combo out-of-sample: {best['band']} band + {best['exit']} exit")
        print(f"  -> across {int(best['tickers'])} tickers, {int(best['trades'])} total trades")
        print(f"  -> median win rate: {best['med_win_rate']}%, median profit factor: {best['med_pf']}")
        print(f"  -> median return per trade (expectancy): {best['med_expectancy']}%")
        print(f"  -> median worst drawdown: {best['med_max_dd']}%")
        print("\nCompare this 'test' row (out-of-sample) against the 'train' row for the")
        print("same band/exit — if test numbers collapse relative to train, the strategy")
        print("was overfit and the train-period numbers were never real.")

if __name__ == "__main__":
    main()
