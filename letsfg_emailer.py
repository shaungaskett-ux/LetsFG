#!/usr/bin/env python3
"""
LetsFG Auto-Emailer
Runs a flight search and emails results with booking hyperlinks to shaun@thatmuscleguy.com.

Usage:
    python letsfg_emailer.py BNE TYO 2026-09-01
    python letsfg_emailer.py BNE TYO 2026-09-01 --currency AUD --limit 10

SMTP config via environment variables:
    LETSFG_SMTP_HOST     (default: smtp.gmail.com)
    LETSFG_SMTP_PORT     (default: 587)
    LETSFG_SMTP_USER     your sender email address
    LETSFG_SMTP_PASS     your email password or app password
    LETSFG_EMAIL_FROM    (defaults to LETSFG_SMTP_USER)
    LETSFG_EMAIL_TO      (default: shaun@thatmuscleguy.com)
"""

import argparse
import asyncio
import json
import os
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# Ensure the SDK is importable when run from the repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "sdk", "python"))

from letsfg.connectors.currency import fetch_rates, _fallback_convert


# ── Currency helpers (mirrors cli.py) ─────────────────────────────────────

def _convert_price(amount, from_cur, to_cur, eur_rates):
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return amount, from_cur or to_cur or ""
    from_cur = (from_cur or "").upper()
    to_cur = (to_cur or "").upper()
    if not from_cur:
        return amount, to_cur
    if not to_cur or from_cur == to_cur:
        return amount, from_cur
    if eur_rates:
        fr = eur_rates.get(from_cur)
        tr = eur_rates.get(to_cur)
        if fr and tr:
            return round((amount / fr) * tr, 2), to_cur
    converted = round(_fallback_convert(amount, from_cur, to_cur), 2)
    return (converted, to_cur) if converted != round(amount, 2) else (amount, from_cur)


def _dur_str(seconds):
    if not seconds:
        return "-"
    h, m = divmod(int(seconds) // 60, 60)
    return f"{h}h {m:02d}m"


def _route_str(leg):
    if not leg:
        return "-"
    route = leg.get("route_str", "")
    if not route:
        segs = leg.get("segments", [])
        if segs:
            codes = [segs[0].get("origin", "")]
            for s in segs:
                codes.append(s.get("destination", ""))
            route = "→".join(c for c in codes if c)
    return route or "-"


def _time_str(leg, pos="dep"):
    if not leg:
        return "-"
    segs = leg.get("segments") or []
    if not segs:
        return "-"
    dt_str = segs[0].get("departure", "") if pos == "dep" else segs[-1].get("arrival", "")
    if not dt_str:
        return "-"
    try:
        time_part = dt_str.split("T")[1][:5] if "T" in dt_str else dt_str[:5]
    except (IndexError, TypeError):
        return "-"
    if pos == "arr":
        dep_str = segs[0].get("departure", "")
        if dep_str and "T" in dep_str and "T" in dt_str:
            try:
                from datetime import datetime as dt
                dep_d = dt.strptime(dep_str.split("T")[0], "%Y-%m-%d").date()
                arr_d = dt.strptime(dt_str.split("T")[0], "%Y-%m-%d").date()
                diff = (arr_d - dep_d).days
                if diff > 0:
                    return f"{time_part}+{diff}"
            except Exception:
                pass
    return time_part


# ── HTML email builder ─────────────────────────────────────────────────────

def build_html_email(offers, origin, destination, date, currency, eur_rates):
    rows = []
    for i, o in enumerate(offers, 1):
        ob = o.get("outbound", {})
        raw_price = o.get("price", 0)
        raw_cur = (o.get("currency", currency) or currency).upper()
        price, cur = _convert_price(raw_price, raw_cur, currency.upper(), eur_rates)

        airline = o.get("owner_airline", "") or (o.get("airlines") or [""])[0]
        route = _route_str(ob)
        depart = _time_str(ob, "dep")
        arrive = _time_str(ob, "arr")
        duration = _dur_str((ob or {}).get("total_duration_seconds"))
        stops = ob.get("stopovers", 0)

        url = o.get("booking_url") or ""
        cond = o.get("conditions") or {}
        ob_url = cond.get("outbound_booking_url") or url
        book_link = f'<a href="{ob_url}" style="color:#1a73e8;font-weight:bold;">Book →</a>' if ob_url else "—"

        price_str = f"{cur} {price:,.2f}"
        row_bg = "#f9fafb" if i % 2 == 0 else "#ffffff"
        rows.append(f"""
        <tr style="background:{row_bg};">
            <td style="padding:10px 12px;color:#555;">{i}</td>
            <td style="padding:10px 12px;font-weight:bold;color:#1d6f3b;">{price_str}</td>
            <td style="padding:10px 12px;">{airline}</td>
            <td style="padding:10px 12px;">{route}</td>
            <td style="padding:10px 12px;">{depart}</td>
            <td style="padding:10px 12px;">{arrive}</td>
            <td style="padding:10px 12px;">{duration}</td>
            <td style="padding:10px 12px;text-align:center;">{stops}</td>
            <td style="padding:10px 12px;">{book_link}</td>
        </tr>""")

    rows_html = "\n".join(rows)
    now = datetime.now().strftime("%d %b %Y, %I:%M %p")
    best_price = ""
    if offers:
        raw_p = offers[0].get("price", 0)
        raw_c = (offers[0].get("currency", currency) or currency).upper()
        p, c = _convert_price(raw_p, raw_c, currency.upper(), eur_rates)
        best_price = f"{c} {p:,.2f}"

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;font-family:Arial,sans-serif;background:#f4f6f8;">
<div style="max-width:820px;margin:30px auto;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.1);">

  <!-- Header -->
  <div style="background:#1d6f3b;padding:28px 32px;">
    <h1 style="margin:0;color:#fff;font-size:22px;">✈️ Flight Search Results</h1>
    <p style="margin:6px 0 0;color:#a8d5b5;font-size:14px;">
      {origin} → {destination} &nbsp;·&nbsp; {date} &nbsp;·&nbsp; {len(offers)} offers found &nbsp;·&nbsp; {now}
    </p>
  </div>

  <!-- Best deal callout -->
  {'<div style="background:#eaf6ee;border-left:4px solid #1d6f3b;padding:14px 32px;font-size:15px;">🏷️ <strong>Best price:</strong> ' + best_price + ' — ' + (_route_str(offers[0].get("outbound", {})) if offers else "") + '</div>' if offers else ""}

  <!-- Table -->
  <div style="padding:24px 32px;">
    <table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;font-size:14px;">
      <thead>
        <tr style="background:#1d6f3b;color:#fff;">
          <th style="padding:10px 12px;text-align:left;">#</th>
          <th style="padding:10px 12px;text-align:left;">Price ({currency.upper()})</th>
          <th style="padding:10px 12px;text-align:left;">Airline</th>
          <th style="padding:10px 12px;text-align:left;">Route</th>
          <th style="padding:10px 12px;text-align:left;">Depart</th>
          <th style="padding:10px 12px;text-align:left;">Arrive</th>
          <th style="padding:10px 12px;text-align:left;">Duration</th>
          <th style="padding:10px 12px;text-align:center;">Stops</th>
          <th style="padding:10px 12px;text-align:left;">Book</th>
        </tr>
      </thead>
      <tbody>
        {rows_html}
      </tbody>
    </table>
  </div>

  <!-- Footer -->
  <div style="background:#f4f6f8;padding:16px 32px;font-size:12px;color:#888;">
    Powered by <strong>LetsFG</strong> — prices sourced directly from airlines, no OTA markup.
    &nbsp;·&nbsp; Search ran at {now}
  </div>
</div>
</body>
</html>"""


# ── Plain text fallback ────────────────────────────────────────────────────

def build_text_email(offers, origin, destination, date, currency, eur_rates):
    lines = [f"Flight Search: {origin} → {destination} on {date}", "=" * 50, ""]
    for i, o in enumerate(offers, 1):
        ob = o.get("outbound", {})
        raw_p = o.get("price", 0)
        raw_c = (o.get("currency", currency) or currency).upper()
        price, cur = _convert_price(raw_p, raw_c, currency.upper(), eur_rates)
        airline = o.get("owner_airline", "") or (o.get("airlines") or [""])[0]
        url = o.get("booking_url") or ""
        cond = o.get("conditions") or {}
        ob_url = cond.get("outbound_booking_url") or url
        lines.append(f"{i:2d}. {cur} {price:,.2f}  {airline}  {_route_str(ob)}  "
                     f"{_time_str(ob,'dep')}→{_time_str(ob,'arr')}  "
                     f"{_dur_str((ob or {}).get('total_duration_seconds'))}  "
                     f"{ob.get('stopovers',0)} stop(s)")
        if ob_url:
            lines.append(f"    Book: {ob_url}")
        lines.append("")
    return "\n".join(lines)


# ── Send email ─────────────────────────────────────────────────────────────

def send_email(subject, html_body, text_body):
    smtp_host = os.environ.get("LETSFG_SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("LETSFG_SMTP_PORT", "587"))
    smtp_user = os.environ.get("LETSFG_SMTP_USER", "")
    smtp_pass = os.environ.get("LETSFG_SMTP_PASS", "")
    from_addr = os.environ.get("LETSFG_EMAIL_FROM", smtp_user)
    to_addr   = os.environ.get("LETSFG_EMAIL_TO", "shaun@thatmuscleguy.com")

    if not smtp_user or not smtp_pass:
        raise ValueError(
            "SMTP credentials not set. Export LETSFG_SMTP_USER and LETSFG_SMTP_PASS.\n"
            "For Gmail use an App Password: https://myaccount.google.com/apppasswords"
        )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(from_addr, [to_addr], msg.as_string())

    return to_addr


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="LetsFG Auto-Emailer")
    parser.add_argument("origin", help="Departure IATA code (e.g. BNE)")
    parser.add_argument("destination", help="Arrival IATA code (e.g. TYO)")
    parser.add_argument("date", help="Departure date YYYY-MM-DD")
    parser.add_argument("--currency", default="AUD", help="Display currency (default: AUD)")
    parser.add_argument("--limit", type=int, default=20, help="Max results (default: 20)")
    parser.add_argument("--mode", default="fast", help="Search mode: fast or default")
    parser.add_argument("--sort", default="price", help="Sort: price or duration")
    parser.add_argument("--dry-run", action="store_true", help="Print email HTML, don't send")
    args = parser.parse_args()

    # ── Run search ────────────────────────────────────────────────────────
    print(f"Searching {args.origin} → {args.destination} on {args.date} ({args.mode} mode)…")

    from letsfg.local import search_local

    async def _run():
        asyncio.get_event_loop().set_exception_handler(lambda loop, ctx: None)
        return await search_local(
            origin=args.origin,
            destination=args.destination,
            date_from=args.date,
            currency=args.currency,
            limit=args.limit,
            mode=args.mode,
        )

    import warnings, logging
    logging.basicConfig(level=logging.ERROR)
    warnings.filterwarnings("ignore", category=ResourceWarning)

    result = asyncio.run(_run())
    offers = result.get("offers", [])

    if not offers:
        print("No offers found — email not sent.")
        sys.exit(0)

    # Sort
    offers.sort(key=lambda o: float(o.get("price", float("inf"))))
    offers = offers[:args.limit]
    print(f"Found {len(offers)} offers. Best: {offers[0].get('currency','?')} {offers[0].get('price','?')}")

    # ── Currency rates ─────────────────────────────────────────────────────
    try:
        eur_rates = asyncio.run(fetch_rates("EUR"))
    except Exception:
        eur_rates = {}

    # ── Build email ────────────────────────────────────────────────────────
    subject = (f"✈️ {args.origin}→{args.destination} {args.date} — "
               f"from {args.currency} {offers[0].get('price','')} ({len(offers)} deals)")

    html_body = build_html_email(offers, args.origin, args.destination, args.date, args.currency, eur_rates)
    text_body = build_text_email(offers, args.origin, args.destination, args.date, args.currency, eur_rates)

    if args.dry_run:
        print("\n--- EMAIL SUBJECT ---")
        print(subject)
        print("\n--- PLAIN TEXT ---")
        print(text_body)
        return

    # ── Send ───────────────────────────────────────────────────────────────
    try:
        to = send_email(subject, html_body, text_body)
        print(f"Email sent to {to} ✓")
    except ValueError as e:
        print(f"\nSMTP not configured: {e}")
        print("\nTo configure, set environment variables:")
        print("  $env:LETSFG_SMTP_USER = 'you@gmail.com'")
        print("  $env:LETSFG_SMTP_PASS = 'your-app-password'")
        print("\nRunning dry-run instead:\n")
        print(text_body)
    except smtplib.SMTPAuthenticationError:
        print("\nSMTP Authentication failed.")
        print("If using Gmail or Google Workspace, you need an App Password:")
        print("  1. Go to myaccount.google.com → Security → 2-Step Verification → App passwords")
        print("  2. Create an app password and use it as LETSFG_SMTP_PASS (no spaces)")
    except smtplib.SMTPException as e:
        print(f"\nSMTP error: {e}")
        print("Check LETSFG_SMTP_HOST, LETSFG_SMTP_PORT, and credentials.")


if __name__ == "__main__":
    main()
