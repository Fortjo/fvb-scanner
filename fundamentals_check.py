#!/usr/bin/env python3
"""
Peter's 5-point checklist has 3 quantifiable criteria beyond the zone
signal itself: (2) 5yr revenue growth, (3) 5yr profitability, (4) assets
>= debt (1:1 minimum, 2:1 ideal). Criterion 1 (smart money zone) is
already what the FVB signal itself is. Criterion 5 (economic moat) is
Peter's own qualitative judgment -- never checked here, stays Forty's
call (see trading-memory.md).

This checks 2-4 for free via yfinance's own financial-statement data
(Ticker.financials / Ticker.balance_sheet) rather than a paid API --
the Financial Datasets connector used for this once before ran out of
credits (2026-11-21) and Forty doesn't want to pay for it, so this is
the standing path going forward.

Only ever called on FRESH signals (the handful of tickers actually
shown in a scan email), never the whole scanned universe -- financials/
balance_sheet are separate network round-trips per ticker on top of the
price-history fetch already done for every ticker in the universe, and
running this for all 160-600+ scanned tickers every week would make the
scan needlessly slow for numbers nobody but the fresh-signal candidates
will ever be shown.

yfinance typically returns ~4 annual periods, not a clean 5 -- "5yr"
below means "as many trailing annual periods as yfinance has," and
that's noted rather than silently treated as exactly 5.
"""
from __future__ import annotations
import pandas as pd
import yfinance as yf


def _row(df: pd.DataFrame, *names: str):
    """First matching row (by any of the given label variants --
    yfinance's statement row names have shifted across versions),
    sorted oldest-first, or None if nothing matched."""
    if df is None or df.empty:
        return None
    for name in names:
        if name in df.index:
            return df.loc[name].sort_index()
    return None


def get_fundamentals(ticker: str) -> dict:
    """Best-effort fundamentals read for one ticker. Returns a dict:
      available         -- False if yfinance had nothing usable
      periods           -- how many annual income-statement periods found
      revenue           -- [(period, value), ...], oldest first
      net_income        -- [(period, value), ...], oldest first
      total_assets      -- most recent total assets, or None
      total_debt        -- most recent total debt, falling back to total
                            liabilities if no debt figure is reported
      revenue_growth_ok -- latest revenue > oldest available revenue
      profitability_ok  -- profitable in all but at most one period
      balance_sheet_ok  -- total_assets >= total_debt
      note              -- human-readable caveat (thin history, fetch
                            error, liabilities-not-debt substitution)
    """
    out = {"available": False, "periods": 0, "revenue": [], "net_income": [],
           "total_assets": None, "total_debt": None,
           "revenue_growth_ok": None, "profitability_ok": None,
           "balance_sheet_ok": None, "note": ""}
    try:
        t = yf.Ticker(ticker)
        fin = t.financials       # annual income statement
        bs = t.balance_sheet     # annual balance sheet
    except Exception as e:
        out["note"] = f"fetch error: {e}"
        return out

    rev = _row(fin, "Total Revenue", "TotalRevenue", "Revenue")
    ni = _row(fin, "Net Income", "NetIncome", "Net Income Common Stockholders")
    if rev is None or ni is None or len(rev) < 2 or len(ni) < 2:
        out["note"] = "insufficient income-statement history from yfinance"
        return out

    out["available"] = True
    out["periods"] = int(min(len(rev), len(ni)))
    out["revenue"] = [(str(getattr(d, "date", lambda: d)()), float(v)) for d, v in rev.items()]
    out["net_income"] = [(str(getattr(d, "date", lambda: d)()), float(v)) for d, v in ni.items()]
    out["revenue_growth_ok"] = bool(rev.iloc[-1] > rev.iloc[0])

    profitable_periods = int((ni > 0).sum())
    out["profitability_ok"] = profitable_periods >= (len(ni) - 1)   # allow one down year

    assets = _row(bs, "Total Assets", "TotalAssets")
    debt = _row(bs, "Total Debt", "TotalDebt")
    liab = _row(bs, "Total Liabilities Net Minority Interest", "Total Liab",
                "TotalLiabilitiesNetMinorityInterest")
    if assets is not None and len(assets):
        out["total_assets"] = float(assets.iloc[-1])
    if debt is not None and len(debt):
        out["total_debt"] = float(debt.iloc[-1])
    elif liab is not None and len(liab):
        out["total_debt"] = float(liab.iloc[-1])
        out["note"] = (out["note"] + " using total liabilities (no debt figure reported)").strip()
    if out["total_assets"] is not None and out["total_debt"] is not None:
        out["balance_sheet_ok"] = out["total_assets"] >= out["total_debt"]

    if out["periods"] < 4:
        out["note"] = (out["note"] + f"; only {out['periods']} annual periods available (not a full 5yr)").strip(" ;")

    return out


def fundamentals_tag(ticker: str) -> dict:
    """Short summary for the scan email: rating in {PASS, MIXED, FAIL,
    N/A} plus a one-line reason, built from get_fundamentals(). Never
    checks moat (Peter's 5th, judgment-only criterion) -- that stays
    Forty's own call."""
    f = get_fundamentals(ticker)
    if not f["available"]:
        return {"rating": "N/A", "reason": f["note"] or "no data", "detail": f}

    checks = [c for c in (f["revenue_growth_ok"], f["profitability_ok"], f["balance_sheet_ok"]) if c is not None]
    passed = sum(1 for c in checks if c)

    reasons = []
    if f["revenue_growth_ok"] is False:
        reasons.append("revenue declining")
    if f["profitability_ok"] is False:
        reasons.append("unprofitable in 2+ of last years")
    if f["balance_sheet_ok"] is False:
        reasons.append("debt exceeds assets")

    if not checks:
        rating = "N/A"
    elif passed == len(checks):
        rating = "PASS"
    elif passed == 0:
        rating = "FAIL"
    else:
        rating = "MIXED"

    reason = ", ".join(reasons) if reasons else "revenue growing, profitable, solid balance sheet"
    if f["note"]:
        reason += f" ({f['note']})"
    return {"rating": rating, "reason": reason, "detail": f}
