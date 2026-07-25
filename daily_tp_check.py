#!/usr/bin/env python3
"""
Daily TP check — runs more often than the weekly scan specifically so a
TP hit doesn't sit unnoticed for up to 6 days. Only emails when at least
one open position has actually crossed its target; otherwise stays
silent (no daily noise for "nothing happened").

Uses the SAME fixed, hybrid TP target as the weekly email and the live
backtest engine (my_positions.compute_entry_tp_target) — not a
recomputed live value, so this can't disagree with what the weekly
email already told you about each position's target.

Meant to be run daily by its own GitHub Actions schedule (separate from
the weekly scan workflow), but runs fine manually too:
    python3 daily_tp_check.py
"""
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import numpy as np
import pandas as pd

import backtest_33fvb as bt
import my_positions as mp


def check_positions():
    """Returns a list of dicts, one per open position that has reached
    or exceeded its TP target as of today's live price."""
    open_df = mp.load_open_positions()
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

        target = mp.compute_entry_tp_target(df, row["entry_date"], entry_price)
        if np.isnan(target):
            continue

        current_price = df["Close"].values[-1]
        current_high = df["High"].values[-1]  # this week's high so far

        if current_high >= target:
            pct_gain = 100 * (target / entry_price - 1)
            hits.append({
                "ticker": tk, "entry_price": entry_price, "target": target,
                "current_price": current_price, "pct_gain": pct_gain,
                "entry_date": row["entry_date"],
            })
    return hits


def build_alert_body(hits: list) -> str:
    lines = [f"TP ALERT — {len(hits)} position(s) have reached their target\n", "=" * 60]
    for h in hits:
        lines.append(f"\n{h['ticker']} — entered {h['entry_date']} at ${h['entry_price']:.2f}")
        lines.append(f"  Target: ${h['target']:.2f}  |  Current: ${h['current_price']:.2f}")
        lines.append(f"  Gain at target: {h['pct_gain']:+.1f}%")
    lines.append("\n" + "=" * 60)
    lines.append("This is a daily check between your weekly scans — the target itself")
    lines.append("hasn't changed, this just tells you sooner that price reached it.")
    lines.append("Remember to update my_positions.csv with the exit once you act on this.")
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
        send_email(f"TP Alert — {len(hits)} position(s) hit target", body)
    else:
        print("No positions have reached their TP target today. No email sent.")
