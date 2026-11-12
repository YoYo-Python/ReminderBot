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

import base64
import json
import logging
import os
from datetime import date, datetime, timedelta
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
GH_TOKEN = os.environ["GH_TOKEN"]
GH_REPO = os.environ.get("GH_REPO", "YoYo-Python/ReminderBot")
STATE_PATH = "state.json"
SEMESTER_END = date(2027, 1, 21)

GH_API = f"https://api.github.com/repos/{GH_REPO}/contents/{STATE_PATH}"
GH_HEADERS = {
    "Authorization": f"Bearer {GH_TOKEN}",
    "Accept": "application/vnd.github+json",
}


def gh_load():
    try:
        r = requests.get(GH_API, headers=GH_HEADERS, timeout=25)
        if r.status_code == 200:
            j = r.json()
            return json.loads(base64.b64decode(j["content"]).decode()), j["sha"]
    except Exception:
        log.exception("state read failed")
    return {}, None


def gh_save(state, sha):
    body = {
        "message": "bot: update state",
        "content": base64.b64encode(json.dumps(state).encode()).decode(),
        "branch": "main",
    }
    if sha:
        body["sha"] = sha
    r = requests.put(GH_API, headers=GH_HEADERS, json=body, timeout=25)
    if r.status_code not in (200, 201):
        log.warning("state save failed: %s %s", r.status_code, r.text[:200])


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


def backlog_status(state):
    b = state.get("backlog", {"left": 21, "total": 21})
    left = b.get("left", 21)
    total = b.get("total", 21)
    if left <= 0:
        return "🎉 *GIT backlog: CLEARED!*\nYou did it — 21 for 21."
    days = max(1, (SEMESTER_END - date.today()).days)
    need = -(-left // days)  # ceil division
    return (
        f"📚 *GIT backlog: {left}/{total} left*\n"
        f"Pace needed: *{need}/day* to clear by Jan 21 (semester end)"
    )


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

    if cmd == "/done":
        try:
            n = max(1, int(arg)) if arg else 1
        except ValueError:
            return "Usage: /done 3  (how many lectures you finished)"
        state, sha = gh_load()
        b = state.get("backlog", {"left": 21, "total": 21})
        b["left"] = max(0, b.get("left", 21) - n)
        state["backlog"] = b
        gh_save(state, sha)
        return f"✅ {n} down!\n\n" + backlog_status(state)

    if cmd == "/backlog":
        state, _ = gh_load()
        return backlog_status(state)

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
        "/week — the next 7 days\n"
        "/done 3 — log finished GIT lectures\n"
        "/backlog — how many are left + pace"
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
