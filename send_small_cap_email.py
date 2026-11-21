#!/usr/bin/env python3
"""
Small-cap weekly scan -> email. Same engine, same email format as
send_scan_email.py (market-regime banner, account summary box, HTML
table with plain-text fallback, CSV attachment), but scanned against
small_cap_tickers.csv instead of the S&P 100 / NASDAQ 100 universe, and
against the small-cap book (small_cap_positions.csv) instead of the main
one.

Ticker source: small_cap_tickers.csv, one ticker per line, no header.
Populate this from the official S&P SmallCap 600 constituent list (e.g.
State Street's SPSM holdings download) rather than hand-picking names —
a fixed, real index membership avoids the cherry-picking bias a manually
curated small-cap list would introduce.

The account summary (equity/cash/slots) comes from my_positions.py's
shared account_state.csv — this scan and the main scan draw on the same
trading account, so there's only one balance to report, not a separate
small-cap-only figure. It currently only counts the MAIN book's open
positions (my_positions.csv) when marking equity; if small-cap positions
should also count against that same capital, that's a follow-up to
my_positions.compute_account_summary(), not something this script
duplicates.

Reads the same email credentials as the main scanner:
    EMAIL_ADDRESS, EMAIL_PASSWORD, EMAIL_TO

Meant to be run by .github/workflows/small_cap_scan.yml, but runs fine
manually too:
    python3 send_small_cap_email.py
"""
import os
import csv
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
import pandas as pd
import scan_current_buys as scb
import backtest_33fvb as bt
import small_cap_positions
import my_positions
import market_regime
import fundamentals_check as fc

TICKER_FILE = "small_cap_tickers.csv"


def load_universe() -> list[str]:
    if not os.path.exists(TICKER_FILE):
        raise FileNotFoundError(
            f"{TICKER_FILE} not found. Populate it with one ticker per "
            f"line (e.g. from the official S&P SmallCap 600 constituent "
            f"list) before running this scan."
        )
    with open(TICKER_FILE) as f:
        return [row[0].strip().upper() for row in csv.reader(f) if row and row[0].strip()]


def build_email_body(fresh_weeks: int = 12, regime_info: dict = None):
    if regime_info is None:
        regime_info = market_regime.get_market_regime()
    universe = load_universe()
    rows = []
    for tk in universe:
        r = scb.scan_ticker(tk, fresh_weeks)
        if r is not None:
            rows.append(r)
    df = pd.DataFrame(rows)
    df.to_csv("small_cap_scan.csv", index=False)

    longs = df[df["position"] == "LONG"].copy()
    longs["fresh"] = longs["fresh"].astype(bool)
    # a ticker already held in the MAIN scanner's book (my_positions.csv)
    # is excluded here too, not just ones tracked in small_cap_positions.csv.
    held_tickers = small_cap_positions.get_open_tickers() | my_positions.get_open_tickers()
    longs = longs[~longs["ticker"].str.upper().isin(held_tickers)]
    fresh = longs[longs["fresh"]].sort_values("hist_expectancy", ascending=False)

    # [FUNDAMENTALS CHECK] same as send_scan_email.py -- Peter's checklist
    # criteria 2-4, free via yfinance, only on the fresh signals shown.
    fresh = fresh.copy()
    fund_info = {tk: fc.fundamentals_tag(tk) for tk in fresh["ticker"]}
    fresh["fund_rating"] = fresh["ticker"].map(lambda tk: fund_info[tk]["rating"])
    fresh["fund_reason"] = fresh["ticker"].map(lambda tk: fund_info[tk]["reason"])

    # same shared-account sizing suggestion as the main scan (see module
    # docstring: one account, one balance, no small-cap-only figure).
    acct = my_positions.compute_account_summary()

    lines = []
    lines.append(f"Small-Cap 33FVB Scan — {len(df)} tickers checked, {len(fresh)} fresh buy signals\n")
    lines.append(market_regime.regime_banner_text(regime_info) + "\n")
    if acct is not None:
        fx_note = "" if acct["fx_live"] else "  ** FX rate fetch failed, using approx rate **"
        lines.append(f"Account: equity €{acct['equity']:.2f} | cash €{acct['cash']:.2f} | "
                      f"{acct['open_count']}/{acct['slots']} slots used | "
                      f"per-slot €{acct['per_slot_eur']:.2f}{fx_note}\n")
    lines.append("=" * 70)

    if len(fresh) == 0:
        lines.append("\nNo fresh buy signals this week.")
    else:
        for _, row in fresh.iterrows():
            below_warn = " ** BELOW STOP LEVEL (close basis) — check chart **" if row.get("below_stop_level") else ""
            thin_warn = " ** THIN BAND SAMPLE — verify on TradingView Monthly chart before acting **" if row.get("thin_sample") else ""
            struct_tag = row.get("daily_structure", "INSUFFICIENT")
            struct_line = f"  Daily structure: {struct_tag}" if struct_tag != "INSUFFICIENT" else ""
            rr = row["risk_reward"]
            rr_str = f"{rr:.1f}" if pd.notna(rr) else "n/a"
            lines.append(f"\n{row['ticker']}  —  {row['rating']}")
            lines.append(f"  Entered {row['entry_date']} ({row['weeks_held']:.0f} wks ago), open P&L {row['open_pl_pct']:+.1f}%")
            lines.append(f"  History: {row['hist_trades']:.0f} trades, {row['hist_win_rate']:.1f}% win rate, "
                          f"{row['hist_expectancy']:+.1f}% expectancy/trade")
            dd_med, dd_worst = row.get("hist_median_dd"), row.get("hist_worst_dd")
            if pd.notna(dd_med):
                lines.append(f"  Typical drawdown before resolving: {dd_med:.1f}% (worst {dd_worst:.1f}%)")
            lines.append(f"  Risk/reward from here: {rr_str}{below_warn}{thin_warn}")
            # [ENTRY STRENGTH] same close_position/entry_strength fields
            # scan_current_buys.scan_ticker() already computes for every
            # row (shared with the main scan) -- just wasn't being shown
            # here before. See send_scan_email.py's comment for the
            # validation history. Shown as a flag, never used to hide a
            # signal — you still see every entry either way.
            cp = row.get("close_position")
            strength = row.get("entry_strength")
            if strength == "STRONG":
                lines.append(f"  Entry strength: STRONG close ({cp:.2f}) — the historically better half")
            elif strength == "WEAK":
                lines.append(f"  Entry strength: WEAK close ({cp:.2f}) — below the validated "
                              f"{bt.CLOSE_POSITION_MIN_THRESHOLD} cutoff, historically the weaker half")
            if struct_line:
                lines.append(struct_line)
            # [FUNDAMENTALS] Peter's checklist 2-4 -- moat (5) is never
            # checked here, that stays your own call.
            fund_rating, fund_reason = row.get("fund_rating"), row.get("fund_reason")
            if fund_rating and fund_rating != "N/A":
                lines.append(f"  Fundamentals (Peter's checklist 2-4): {fund_rating} — {fund_reason}")
            elif fund_rating == "N/A":
                lines.append(f"  Fundamentals (Peter's checklist 2-4): N/A — {fund_reason}")
            if acct is not None:
                if acct["available_slots"] > 0:
                    lines.append(f"  Suggested buy size: €{acct['suggested_buy_eur']:.2f} "
                                  f"({acct['available_slots']} slot(s) free)")
                else:
                    lines.append("  ** NO FREE SLOT — all slots full, would need to close a position first **")

    lines.append(small_cap_positions.build_positions_section())
    lines.append(small_cap_positions.build_closed_positions_section())

    lines.append("\n" + "=" * 70)
    lines.append("Full data attached as CSV. Reminder: this is historical backtest")
    lines.append("performance, not a guarantee — check each chart before acting.")
    return "\n".join(lines), fresh, len(df), acct, regime_info


def build_email_body_html(fresh: pd.DataFrame, total_checked: int, acct: dict = None,
                           regime_info: dict = None) -> str:
    """HTML version of the fresh-signals section, plus the HTML small-cap
    position/closed-trade tables — same layout as send_scan_email.py's
    build_email_body_html(). scan_ticker() computes below_stop_level and
    close_position/entry_strength for every row regardless of universe
    (shared function), so both are shown here the same way as the main
    scan's email now too."""
    rows_html = []
    for _, row in fresh.iterrows():
        rr = row["risk_reward"]
        rr_str = f"{rr:.1f}" if pd.notna(rr) else "n/a"
        below_warn = ('<br><span style="color:#c0392b;font-weight:bold;">&#9888; BELOW STOP LEVEL '
                      '(close basis) — check chart</span>') if row.get("below_stop_level") else ""
        thin_warn = ('<br><span style="color:#b8860b;font-weight:bold;">&#9888; THIN BAND SAMPLE — verify on TradingView Monthly chart</span>') if row.get("thin_sample") else ""
        struct_tag = row.get("daily_structure", "INSUFFICIENT")
        struct_colors = {"BULLISH": "#1e8449", "MIXED": "#7d7d00", "BEARISH": "#c0392b"}
        struct_html = (f'<br><span style="color:{struct_colors.get(struct_tag,"#555")};">'
                       f'Daily structure: {struct_tag}</span>') if struct_tag != "INSUFFICIENT" else ""
        cp, strength = row.get("close_position"), row.get("entry_strength")
        if strength == "STRONG":
            strength_html = (f'<br><span style="color:#1e8449;font-weight:bold;">'
                              f'&#10003; STRONG close ({cp:.2f})</span>')
        elif strength == "WEAK":
            strength_html = (f'<br><span style="color:#b8860b;font-weight:bold;">'
                              f'&#9888; WEAK close ({cp:.2f}) — below {bt.CLOSE_POSITION_MIN_THRESHOLD} cutoff</span>')
        else:
            strength_html = ""
        fund_rating, fund_reason = row.get("fund_rating"), row.get("fund_reason")
        fund_colors = {"PASS": "#1e8449", "MIXED": "#b8860b", "FAIL": "#c0392b"}
        if fund_rating in fund_colors:
            fund_html = (f'<br><span style="color:{fund_colors[fund_rating]};font-weight:bold;">'
                         f'Fundamentals: {fund_rating} — {fund_reason}</span>')
        elif fund_rating == "N/A":
            fund_html = f'<br><span style="color:#888;">Fundamentals: N/A — {fund_reason}</span>'
        else:
            fund_html = ""
        pl_color = "#1a7a1a" if row["open_pl_pct"] >= 0 else "#c0392b"
        size_cell = ""
        if acct is not None:
            if acct["available_slots"] > 0:
                size_cell = (f'<td style="padding:8px;border-bottom:1px solid #ddd;color:#1a7a1a;font-weight:bold;">'
                             f'&euro;{acct["suggested_buy_eur"]:.2f}</td>')
            else:
                size_cell = ('<td style="padding:8px;border-bottom:1px solid #ddd;color:#c0392b;font-weight:bold;">'
                             'no free slot</td>')
        rows_html.append(f"""
        <tr>
          <td style="padding:8px;border-bottom:1px solid #ddd;"><b>{row['ticker']}</b><br>
              <span style="color:#666;font-size:12px;">{row['rating']}</span></td>
          <td style="padding:8px;border-bottom:1px solid #ddd;">{row['entry_date']}<br>
              <span style="color:#666;font-size:12px;">{row['weeks_held']:.0f}wks ago</span></td>
          <td style="padding:8px;border-bottom:1px solid #ddd;color:{pl_color};font-weight:bold;">
              {row['open_pl_pct']:+.1f}%</td>
          <td style="padding:8px;border-bottom:1px solid #ddd;">{row['hist_trades']:.0f} trades<br>
              <span style="color:#666;font-size:12px;">{row['hist_win_rate']:.1f}% win, {row['hist_expectancy']:+.1f}% exp.<br>
              {('Typical DD ' + format(row['hist_median_dd'], '.1f') + '% (worst ' + format(row['hist_worst_dd'], '.1f') + '%)') if pd.notna(row.get('hist_median_dd')) else ''}</span></td>
          <td style="padding:8px;border-bottom:1px solid #ddd;">{rr_str}{below_warn}{thin_warn}{struct_html}{strength_html}{fund_html}</td>
          {size_cell}
        </tr>""")

    size_header = '<th style="padding:8px;text-align:left;">Suggested buy</th>' if acct is not None else ""

    fresh_table = ""
    if len(fresh) == 0:
        fresh_table = "<p>No fresh buy signals this week.</p>"
    else:
        fresh_table = f"""
        <table style="border-collapse:collapse;width:100%;font-family:Arial,sans-serif;font-size:14px;">
          <tr style="background-color:#333;color:white;">
            <th style="padding:8px;text-align:left;">Ticker</th>
            <th style="padding:8px;text-align:left;">Entered</th>
            <th style="padding:8px;text-align:left;">Open P&amp;L</th>
            <th style="padding:8px;text-align:left;">History</th>
            <th style="padding:8px;text-align:left;">Risk/Reward</th>
            {size_header}
          </tr>
          {"".join(rows_html)}
        </table>"""

    regime_html = ""
    if regime_info is not None:
        regime_html = market_regime.regime_banner_html(regime_info)

    acct_html = ""
    if acct is not None:
        fx_warn_html = ('<br><span style="color:#c0392b;">&#9888; FX rate fetch failed, using approx rate</span>'
                         ) if not acct["fx_live"] else ""
        acct_html = f"""
        <p style="background-color:#f0f4f8;padding:8px 12px;border-radius:4px;font-size:13px;color:#333;">
          <b>Account:</b> equity &euro;{acct['equity']:.2f} &nbsp;|&nbsp; cash &euro;{acct['cash']:.2f} &nbsp;|&nbsp;
          {acct['open_count']}/{acct['slots']} slots used &nbsp;|&nbsp; per-slot &euro;{acct['per_slot_eur']:.2f}{fx_warn_html}
        </p>"""

    html = f"""
    <html><body style="font-family:Arial,sans-serif;color:#222;max-width:800px;margin:0 auto;">
      <h1 style="font-size:20px;border-bottom:2px solid #333;padding-bottom:8px;">
        Small-Cap 33FVB Scan — {total_checked} tickers checked, {len(fresh)} fresh buy signals
      </h1>
      {regime_html}
      {acct_html}
      {fresh_table}
      {small_cap_positions.build_positions_section_html()}
      {small_cap_positions.build_closed_positions_section_html()}
      <p style="margin-top:30px;color:#666;font-size:12px;border-top:1px solid #ddd;padding-top:10px;">
        Full data attached as CSV. Reminder: this is historical backtest performance,
        not a guarantee — check each chart before acting.
      </p>
    </body></html>"""
    return html


def send_email(body: str, html_body: str):
    addr = os.environ["EMAIL_ADDRESS"]
    pwd = os.environ["EMAIL_PASSWORD"]
    to = os.environ.get("EMAIL_TO", addr)

    msg = MIMEMultipart("mixed")
    msg["From"] = addr
    msg["To"] = to
    msg["Subject"] = "Small-Cap 33FVB Scan Results"

    # multipart/alternative: email clients that render HTML show the
    # pretty version; anything that can't falls back to plain text.
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(body, "plain"))
    alt.attach(MIMEText(html_body, "html"))
    msg.attach(alt)

    with open("small_cap_scan.csv", "rb") as f:
        part = MIMEApplication(f.read(), Name="small_cap_scan.csv")
    part["Content-Disposition"] = 'attachment; filename="small_cap_scan.csv"'
    msg.attach(part)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(addr, pwd)
        server.sendmail(addr, to, msg.as_string())
    print("Small-cap scan email sent.")


if __name__ == "__main__":
    regime_info = market_regime.get_market_regime()
    body, fresh, total_checked, acct, regime_info = build_email_body(regime_info=regime_info)
    html_body = build_email_body_html(fresh, total_checked, acct, regime_info)
    print(body)  # also visible in the GitHub Actions log
    send_email(body, html_body)
