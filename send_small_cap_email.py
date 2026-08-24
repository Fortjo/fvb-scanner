#!/usr/bin/env python3
"""
Small-cap weekly scan -> email. Same engine, same email format as
send_scan_email.py, but scanned against small_cap_tickers.csv instead of
the S&P 100 / NASDAQ 100 universe.

Ticker source: small_cap_tickers.csv, one ticker per line, no header.
Populate this from the official S&P SmallCap 600 constituent list (e.g.
State Street's SPSM holdings download) rather than hand-picking names —
a fixed, real index membership avoids the cherry-picking bias a manually
curated small-cap list would introduce.

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
import pandas as pd
import scan_current_buys as scb
import backtest_33fvb as bt
import small_cap_positions

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


def build_email_body(fresh_weeks: int = 12) -> str:
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
    held_tickers = small_cap_positions.get_open_tickers()
    longs = longs[~longs["ticker"].str.upper().isin(held_tickers)]
    fresh = longs[longs["fresh"]].sort_values("hist_expectancy", ascending=False)

    lines = []
    lines.append(f"Small-Cap 33FVB Scan — {len(df)} tickers checked, {len(fresh)} fresh buy signals\n")
    lines.append("=" * 70)

    if len(fresh) == 0:
        lines.append("\nNo fresh buy signals this week.")
    else:
        for _, r in fresh.iterrows():
            lines.append(
                f"\n{r['ticker']}\n"
                f"  Entered {r['entry_date']} ({r['weeks_held']}wks ago)\t"
                f"Open P&L {r['open_pl_pct']:.1f}%\n"
                f"  History: {r['hist_trades']} trades, "
                f"{r['hist_win_rate']:.1f}% win, {r['hist_expectancy']:+.1f}% exp.\t"
                f"R/R {r['risk_reward']}"
            )

    positions = small_cap_positions.load()
    lines.append("\n\n" + "=" * 70)
    lines.append("Your Small-Cap Positions")
    if positions.empty:
        lines.append("\nNo open small-cap positions yet.")
    else:
        for _, p in positions.iterrows():
            lines.append(f"\n{p['ticker']}\t{p.to_dict()}")

    lines.append(
        "\n\nFull data attached as CSV. Reminder: this is historical backtest "
        "performance, not a guarantee — check each chart before acting."
    )
    return "\n".join(lines)


def send_email(body: str):
    email_address = os.environ["EMAIL_ADDRESS"]
    email_password = os.environ["EMAIL_PASSWORD"]
    email_to = os.environ["EMAIL_TO"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Small-Cap 33FVB Scan Results"
    msg["From"] = email_address
    msg["To"] = email_to
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(email_address, email_password)
        server.sendmail(email_address, email_to, msg.as_string())


if __name__ == "__main__":
    body = build_email_body()
    send_email(body)
    print("Small-cap scan email sent.")
