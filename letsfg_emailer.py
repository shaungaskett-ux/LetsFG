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
    cards = []
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
        stops_str = f"{stops} stop{'s' if stops != 1 else ''}"

        url = o.get("booking_url") or ""
        cond = o.get("conditions") or {}
        ob_url = cond.get("outbound_booking_url") or url

        price_str = f"{cur} {price:,.2f}"
        card_bg = "#f9fafb" if i % 2 == 0 else "#ffffff"

        book_btn = (
            f'<a href="{ob_url}" style="display:inline-block;background:#1d6f3b;color:#ffffff;'
            f'font-weight:bold;font-size:14px;padding:10px 20px;border-radius:6px;'
            f'text-decoration:none;">Book now →</a>'
            if ob_url else
            '<span style="color:#aaa;font-size:13px;">No link</span>'
        )

        cards.append(f"""
<table width="100%" cellspacing="0" cellpadding="0" style="background:{card_bg};border-bottom:1px solid #e5e7eb;">
  <tr>
    <td style="padding:16px 20px;">
      <!-- Price + airline row -->
      <table width="100%" cellspacing="0" cellpadding="0">
        <tr>
          <td style="font-size:20px;font-weight:bold;color:#1d6f3b;">{price_str}</td>
          <td style="text-align:right;font-size:13px;color:#555;">{airline}</td>
        </tr>
      </table>
      <!-- Route row -->
      <p style="margin:6px 0 2px;font-size:15px;font-weight:bold;color:#111;">{route}</p>
      <!-- Times + duration row -->
      <p style="margin:0 0 10px;font-size:13px;color:#555;">
        {depart} → {arrive} &nbsp;·&nbsp; {duration} &nbsp;·&nbsp; {stops_str}
      </p>
      <!-- Book button -->
      {book_btn}
    </td>
  </tr>
</table>""")

    cards_html = "\n".join(cards)
    now = datetime.now().strftime("%d %b %Y, %I:%M %p")
    best_price = ""
    if offers:
        raw_p = offers[0].get("price", 0)
        raw_c = (offers[0].get("currency", currency) or currency).upper()
        p, c = _convert_price(raw_p, raw_c, currency.upper(), eur_rates)
        best_price = f"{c} {p:,.2f}"

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;font-family:Arial,sans-serif;background:#f4f6f8;">
<table width="100%" cellspacing="0" cellpadding="0" style="background:#f4f6f8;">
  <tr>
    <td align="center" style="padding:20px 12px;">
      <table width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.1);">

        <!-- Header -->
        <tr>
          <td style="background:#1d6f3b;padding:24px 20px;">
            <p style="margin:0;color:#fff;font-size:20px;font-weight:bold;">&#9992;&#65039; Flight Results</p>
            <p style="margin:6px 0 0;color:#a8d5b5;font-size:13px;">
              {origin} &rarr; {destination} &nbsp;&middot;&nbsp; {date} &nbsp;&middot;&nbsp; {len(offers)} offers &nbsp;&middot;&nbsp; {now}
            </p>
          </td>
        </tr>

        <!-- Best deal -->
        {'<tr><td style="background:#eaf6ee;border-left:4px solid #1d6f3b;padding:12px 20px;font-size:14px;">&#127991;&#65039; <strong>Best price:</strong> ' + best_price + ' &mdash; ' + (_route_str(offers[0].get("outbound", {})) if offers else "") + '</td></tr>' if offers else ""}

        <!-- Cards -->
        <tr>
          <td>
            {cards_html}
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="background:#f4f6f8;padding:14px 20px;font-size:11px;color:#888;">
            Powered by <strong>LetsFG</strong> &mdash; prices sourced directly from airlines, no OTA markup.
          </td>
        </tr>

      </table>
    </td>
  </tr>
</table>
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
