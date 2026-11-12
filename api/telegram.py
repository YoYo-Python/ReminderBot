"""
Telegram command endpoint (webhook). Telegram POSTs updates here.

Commands:
  /today            - everything scheduled today
  /tomorrow         - everything scheduled tomorrow
  /day 20           - a specific day (this month; rolls to next month if past)
  /day 2026-11-20   - a specific day (full date)
  /week             - the next 7 days grouped

Only your CHAT_ID is allowed to use it.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from icalendar import Calendar
from flask import Flask, request

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("telegram")

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
CALENDAR_URLS = json.loads(os.environ.get("CALENDAR_URLS", "{}"))
TZ = ZoneInfo(os.environ.get("TIMEZONE", "Africa/Cairo"))

app = Flask(__name__)


def tg_send(chat_id, text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=25,
        )
    except Exception:
        log.exception("send failed")


def fetch_events(days):
    now = datetime.now(TZ)
    horizon = now + timedelta(days=days)
    events = []
    for name, url in CALENDAR_URLS.items():
        try:
            r = requests.get(url, timeout=25)
            r.raise_for_status()
            cal = Calendar.from_ical(r.text)
        except Exception as e:
            log.warning("feed %s failed: %s", name, e)
            continue
        for comp in cal.walk("VEVENT"):
            try:
                start = comp.decoded("dtstart")
                end = comp.decoded("dtend")
            except Exception:
                continue
            if not isinstance(start, datetime):
                continue
            if start.tzinfo is None:
                start = start.replace(tzinfo=TZ)
                end = end.replace(tzinfo=TZ)
            if now - timedelta(hours=1) <= start < horizon:
                events.append(
                    {
                        "calendar": name,
                        "summary": str(comp.get("summary", "Busy")),
                        "start": start.astimezone(TZ),
                        "end": end.astimezone(TZ),
                    }
                )
    events.sort(key=lambda e: e["start"])
    return events


def fmt_day(events, day):
    day_events = [e for e in events if e["start"].date() == day]
    if not day_events:
        return "🌙 Nothing scheduled — free day."
    lines = []
    for e in day_events:
        lines.append(f"• {e['start']:%H:%M}–{e['end']:%H:%M} — {e['summary']} _({e['calendar']})_")
    return "\n".join(lines)


def parse_day(arg):
    today = datetime.now(TZ).date()
    arg = arg.strip()
    for fmt in ("%Y-%m-%d", "%d.%m", "%d/%m", "%d-%m"):
        try:
            d = datetime.strptime(arg, fmt).date()
            if fmt != "%Y-%m-%d":
                d = d.replace(year=today.year)
                if d < today:
                    d = d.replace(year=today.year + 1)
            return d
        except ValueError:
            continue
    try:
        n = int(arg)
        d = today.replace(day=n)
        if d < today:
            d = d.replace(month=today.month + 1) if today.month < 12 else d.replace(year=today.year + 1, month=1)
        return d
    except ValueError:
        return None


def handle(text):
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower().split("@")[0]
    arg = parts[1] if len(parts) > 1 else ""

    if cmd == "/today":
        day = datetime.now(TZ).date()
        events = fetch_events(days=2)
        return f"📅 *Today — {day:%a %d %b}*\n\n" + fmt_day(events, day)

    if cmd == "/tomorrow":
        day = datetime.now(TZ).date() + timedelta(days=1)
        events = fetch_events(days=3)
        return f"📅 *Tomorrow — {day:%a %d %b}*\n\n" + fmt_day(events, day)

    if cmd == "/day":
        day = parse_day(arg) if arg else None
        if day is None:
            return "Usage: /day 20  (or /day 2026-11-20)"
        events = fetch_events(days=45)
        return f"📅 *{day:%A, %d %b %Y}*\n\n" + fmt_day(events, day)

    if cmd == "/week":
        events = fetch_events(days=8)
        today = datetime.now(TZ).date()
        blocks = []
        for i in range(7):
            day = today + timedelta(days=i)
            body = fmt_day(events, day)
            label = f"*{day:%a %d %b}*" + (" (today)" if i == 0 else "")
            blocks.append(f"{label}\n{body}")
        return "🗓 *Next 7 days*\n\n" + "\n\n".join(blocks)

    return (
        "Commands:\n"
        "/today — what's on today\n"
        "/tomorrow — what's on tomorrow\n"
        "/day 20 — a specific day\n"
        "/week — the next 7 days"
    )


@app.route("/api/telegram", methods=["POST"])
def webhook():
    try:
        update = request.get_json(force=True, silent=True) or {}
        msg = update.get("message") or {}
        chat_id = str(msg.get("chat", {}).get("id", ""))
        text = (msg.get("text") or "").strip()
        if not chat_id or not text:
            return "ok"
        if chat_id != CHAT_ID:
            tg_send(chat_id, "🚫 Not authorized.")
            return "ok"
        if text.startswith("/"):
            tg_send(chat_id, handle(text))
    except Exception:
        log.exception("webhook failed")
    return "ok"
