#!/usr/bin/env python3
"""
Weekly scan -> email. Runs the full scanner, formats a clean summary
(fresh buys sorted by track record, with risk/reward and the below-stop
warning), and emails it via Gmail SMTP.

Reads credentials from environment variables (never hardcode these):
    EMAIL_ADDRESS   - the Gmail address sending the email
    EMAIL_PASSWORD  - a Gmail APP PASSWORD (not your real password —
                      see setup instructions)
    EMAIL_TO        - where to send the summary (can be the same address)

Meant to be run by a GitHub Actions scheduled workflow (see
.github/workflows/weekly_scan.yml), but runs fine manually too:
    python3 send_scan_email.py
"""
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import pandas as pd
import scan_current_buys as scb
import backtest_33fvb as bt
import my_positions


def build_email_body(fresh_weeks: int = 12) -> str:
    rows = []
    for tk in bt.UNIVERSE:
        r = scb.scan_ticker(tk, fresh_weeks)
        if r is not None:
            rows.append(r)
    df = pd.DataFrame(rows)
    df.to_csv("current_buy_scan.csv", index=False)

    longs = df[df["position"] == "LONG"].copy()
    longs["fresh"] = longs["fresh"].astype(bool)
    # [HELD-POSITION FILTER] don't flag a "new" buy signal for something
    # you're already holding — no point cluttering the list with it.
    held_tickers = my_positions.get_open_tickers()
    longs = longs[~longs["ticker"].str.upper().isin(held_tickers)]
    fresh = longs[longs["fresh"]].sort_values("hist_expectancy", ascending=False)

    lines = []
    lines.append(f"Weekly 33FVB scan — {len(df)} tickers checked, {len(fresh)} fresh buy signals\n")
    lines.append("=" * 70)

    if len(fresh) == 0:
        lines.append("\nNo fresh buy signals this week.")
    else:
        for _, row in fresh.iterrows():
            below_warn = " ** BELOW STOP LEVEL (close basis) — check chart **" if row.get("below_stop_level") else ""
            rr = row["risk_reward"]
            rr_str = f"{rr:.1f}" if pd.notna(rr) else "n/a"
            lines.append(f"\n{row['ticker']}  —  {row['rating']}")
            lines.append(f"  Entered {row['entry_date']} ({row['weeks_held']:.0f} wks ago), open P&L {row['open_pl_pct']:+.1f}%")
            lines.append(f"  History: {row['hist_trades']:.0f} trades, {row['hist_win_rate']:.1f}% win rate, "
                          f"{row['hist_expectancy']:+.1f}% expectancy/trade")
            lines.append(f"  Risk/reward from here: {rr_str}{below_warn}")

    lines.append(my_positions.build_positions_section())
    lines.append(my_positions.build_closed_positions_section())

    lines.append("\n" + "=" * 70)
    lines.append("Full data attached as CSV. Reminder: this is historical backtest")
    lines.append("performance, not a guarantee — check each chart before acting.")
    return "\n".join(lines), fresh, len(df), fresh


def build_email_body_html(fresh: pd.DataFrame, total_checked: int) -> str:
    """HTML version of the fresh-signals section, plus the HTML position/
    closed-trade tables from my_positions.py. Same underlying data as the
    plain-text version — just formatted for an actual email client."""
    rows_html = []
    for _, row in fresh.iterrows():
        rr = row["risk_reward"]
        rr_str = f"{rr:.1f}" if pd.notna(rr) else "n/a"
        below_warn = ('<br><span style="color:#c0392b;font-weight:bold;">&#9888; BELOW STOP LEVEL '
                      '(close basis) — check chart</span>') if row.get("below_stop_level") else ""
        pl_color = "#1a7a1a" if row["open_pl_pct"] >= 0 else "#c0392b"
        rows_html.append(f"""
        <tr>
          <td style="padding:8px;border-bottom:1px solid #ddd;"><b>{row['ticker']}</b><br>
              <span style="color:#666;font-size:12px;">{row['rating']}</span></td>
          <td style="padding:8px;border-bottom:1px solid #ddd;">{row['entry_date']}<br>
              <span style="color:#666;font-size:12px;">{row['weeks_held']:.0f}wks ago</span></td>
          <td style="padding:8px;border-bottom:1px solid #ddd;color:{pl_color};font-weight:bold;">
              {row['open_pl_pct']:+.1f}%</td>
          <td style="padding:8px;border-bottom:1px solid #ddd;">{row['hist_trades']:.0f} trades<br>
              <span style="color:#666;font-size:12px;">{row['hist_win_rate']:.1f}% win, {row['hist_expectancy']:+.1f}% exp.</span></td>
          <td style="padding:8px;border-bottom:1px solid #ddd;">{rr_str}{below_warn}</td>
        </tr>""")

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
          </tr>
          {"".join(rows_html)}
        </table>"""

    html = f"""
    <html><body style="font-family:Arial,sans-serif;color:#222;max-width:800px;margin:0 auto;">
      <h1 style="font-size:20px;border-bottom:2px solid #333;padding-bottom:8px;">
        Weekly 33FVB Scan — {total_checked} tickers checked, {len(fresh)} fresh buy signals
      </h1>
      {fresh_table}
      {my_positions.build_positions_section_html()}
      {my_positions.build_closed_positions_section_html()}
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
    msg["Subject"] = "Weekly 33FVB Scan Results"

    # multipart/alternative: email clients that render HTML show the
    # pretty version; anything that can't falls back to plain text.
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(body, "plain"))
    alt.attach(MIMEText(html_body, "html"))
    msg.attach(alt)

    with open("current_buy_scan.csv", "rb") as f:
        from email.mime.application import MIMEApplication
        part = MIMEApplication(f.read(), Name="current_buy_scan.csv")
    part["Content-Disposition"] = 'attachment; filename="current_buy_scan.csv"'
    msg.attach(part)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(addr, pwd)
        server.sendmail(addr, to, msg.as_string())
    print("Email sent.")


if __name__ == "__main__":
    body, fresh, total_checked = build_email_body()
    html_body = build_email_body_html(fresh, total_checked)
    print(body)  # also visible in the GitHub Actions log
    send_email(body, html_body)
