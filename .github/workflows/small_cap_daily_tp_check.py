#!/usr/bin/env python3
"""
Small-cap daily TP check — same purpose as daily_tp_check.py, just
pointed at small_cap_positions.csv instead of my_positions.csv, so a
small-cap TP hit doesn't sit unnoticed until the next Saturday scan.

Only emails when at least one open small-cap position has actually
crossed its target; otherwise stays silent.

Meant to be run daily by its own GitHub Actions schedule, but runs fine
manually too:
    python3 small_cap_daily_tp_check.py
"""
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import numpy as np
import pandas as pd

import backtest_33fvb as bt
import small_cap_positions as scp


def check_positions():
    open_df = scp.load_open_positions()
    if open_df is None or len(open_df) == 0:
        return []

    hits = []
    for _, row in open_df.iterrows():
        tk = row["ticker"].strip()
        try:
            entry_price = float(row["entry_price"])
        except (ValueError, TypeError):
            continue

        try:
            raw = bt.fetch(tk)
        except Exception:
            continue
        if raw is None:
            continue

        df = bt.prep(raw)
        if len(df) < bt.NBAR_LEN + 20:
            continue

        target = scp.compute_entry_tp_target(df, row["entry_date"], entry_price)
        if np.isnan(target):
            continue

        current_price = df["Close"].values[-1]
        current_high = df["High"].values[-1]

        if current_high >= target:
            pct_gain = 100 * (target / entry_price - 1)
            hits.append({
                "ticker": tk, "entry_price": entry_price, "target": target,
                "current_price": current_price, "pct_gain": pct_gain,
                "entry_date": row["entry_date"],
            })
    return hits


def build_alert_body(hits: list) -> str:
    lines = [f"SMALL-CAP TP ALERT — {len(hits)} position(s) have reached their target\n", "=" * 60]
    for h in hits:
        lines.append(f"\n{h['ticker']} — entered {h['entry_date']} at ${h['entry_price']:.2f}")
        lines.append(f"  Target: ${h['target']:.2f}  |  Current: ${h['current_price']:.2f}")
        lines.append(f"  Gain at target: {h['pct_gain']:+.1f}%")
    lines.append("\n" + "=" * 60)
    lines.append("This is a daily check between your Saturday small-cap scans — the")
    lines.append("target itself hasn't changed, this just tells you sooner that price")
    lines.append("reached it.")
    lines.append("Remember to update small_cap_positions.csv with the exit once you act on this.")
    return "\n".join(lines)


def send_email(subject: str, body: str):
    addr = os.environ["EMAIL_ADDRESS"]
    pwd = os.environ["EMAIL_PASSWORD"]
    to = os.environ.get("EMAIL_TO", addr)

    msg = MIMEMultipart()
    msg["From"] = addr
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(addr, pwd)
        server.sendmail(addr, to, msg.as_string())
    print("Email sent.")


if __name__ == "__main__":
    hits = check_positions()
    if hits:
        body = build_alert_body(hits)
        print(body)
        send_email(f"Small-Cap TP Alert — {len(hits)} position(s) hit target", body)
    else:
        print("No small-cap positions have reached their TP target today. No email sent.")
