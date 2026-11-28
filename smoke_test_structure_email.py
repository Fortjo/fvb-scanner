#!/usr/bin/env python3
"""One-off smoke test: build the email body (no sending) and print just the
tickers that show the new weekly-structure / confirmed lines, to verify the
Daily+Weekly structure addition renders correctly before relying on it."""
import send_scan_email as sem

body, fresh, total_checked, acct, regime_info = sem.build_email_body()
print(f"Checked {total_checked} tickers, {len(fresh)} fresh signals\n")
print(body)
