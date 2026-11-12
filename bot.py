"""
Telegram Task Reminder Bot
Reads iCloud public calendar feeds (webcal -> https) and sends a Telegram
message REMIND_MINUTES before every event. Runs as a single web service
(Render/Koyeb free tier friendly: includes a / health endpoint).

Env vars:
  TELEGRAM_TOKEN   - from @BotFather (required)
  CALENDAR_URLS    - JSON dict {"Calendar Name": "https://...ics"} (required)
  REMIND_MINUTES   - minutes before event to remind (default 30)
  PORT             - health port (Render sets this automatically)
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

import requests
from icalendar import Calendar

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("bot")

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CALENDAR_URLS = json.loads(os.environ.get("CALENDAR_URLS", "{}"))
REMIND_MINUTES = int(os.environ.get("REMIND_MINUTES", "30"))
PORT = int(os.environ.get("PORT", "8080"))
TZ = ZoneInfo(os.environ.get("TIMEZONE", "Africa/Cairo"))

STATE_FILE = os.environ.get("STATE_FILE", "state.json")
API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# ---------------------------------------------------------------- state ----

_lock = threading.Lock()


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"chat_ids": [], "sent": {}}


def save_state(state):
    with _lock:
        # prune old sent-keys so the file doesn't grow forever
        now = datetime.now(TZ)
        cutoff = now - timedelta(days=7)
        kept = {}
        for k in state.get("sent", {}):
            try:
                start = datetime.fromisoformat(k.split("|", 1)[1])
            except Exception:
                continue
            if start >= cutoff:
                kept[k] = True
        state["sent"] = kept
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)


# ------------------------------------------------------------ telegram ----

def tg(method, **params):
    try:
        r = requests.post(f"{API}/{method}", json=params, timeout=30)
        return r.json()
    except Exception as e:
        log.warning("telegram %s failed: %s", method, e)
        return {}


def broadcast(text):
    state = load_state()
    for chat_id in state["chat_ids"]:
        tg("sendMessage", chat_id=chat_id, text=text, parse_mode="Markdown")


def poll_commands():
    """Long-poll /start so we learn the chat_id(s) to notify."""
    offset = 0
    while True:
        data = tg(
            "getUpdates",
            offset=offset,
            timeout=50,
            allowed_updates=["message"],
        )
        for upd in data.get("result", []):
            offset = upd["update_id"] + 1
            msg = upd.get("message") or {}
            chat_id = msg.get("chat", {}).get("id")
            text = (msg.get("text") or "").strip()
            if not chat_id or not text.startswith("/"):
                continue

            state = load_state()
            if chat_id not in state["chat_ids"]:
                state["chat_ids"].append(chat_id)
                save_state(state)

            cmd = text.split()[0].split("@")[0]
            if cmd == "/start":
                tg(
                    "sendMessage",
                    chat_id=chat_id,
                    text=(
                        "👋 I'm your task reminder bot.\n"
                        f"I'll message you *{REMIND_MINUTES} minutes* before "
                        "every event in your iCloud calendars.\n"
                        f"Chat ID: `{chat_id}`\n\n"
                        "Commands:\n"
                        "/tasks - show my next 5 upcoming tasks"
                    ),
                    parse_mode="Markdown",
                )
            elif cmd == "/tasks":
                events = fetch_events(hours=48)
                if not events:
                    tg("sendMessage", chat_id=chat_id, text="No upcoming tasks in the next 48 h.")
                    continue
                lines = ["📅 *Next 5 tasks:*"]
                for ev in events[:5]:
                    s = ev["start"].astimezone(TZ)
                    e = ev["end"].astimezone(TZ)
                    lines.append(
                        f"• *{ev['summary']}* ({ev['calendar']})\n"
                        f"  {s:%a %d %b, %H:%M}–{e:%H:%M}"
                    )
                tg("sendMessage", chat_id=chat_id, text="\n".join(lines), parse_mode="Markdown")


# ------------------------------------------------------------- calendars ---

def fetch_events(hours=48):
    """Fetch all events starting within the next `hours` from every feed."""
    now = datetime.now(TZ)
    horizon = now + timedelta(hours=hours)
    events = []
    for name, url in CALENDAR_URLS.items():
        try:
            r = requests.get(url, timeout=30)
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
            # skip all-day events (date, not datetime)
            if not isinstance(start, datetime) or not isinstance(end, datetime):
                continue
            if start.tzinfo is None:
                start = start.replace(tzinfo=TZ)
                end = end.replace(tzinfo=TZ)
            if now <= start < horizon:
                events.append(
                    {
                        "calendar": name,
                        "summary": str(comp.get("summary", "Busy")),
                        "uid": str(comp.get("uid", start.isoformat())),
                        "start": start,
                        "end": end,
                    }
                )
    events.sort(key=lambda e: e["start"])
    return events


def reminder_loop():
    state = load_state()
    while True:
        try:
            now = datetime.now(TZ)
            for ev in fetch_events(hours=36):
                remind_at = ev["start"] - timedelta(minutes=REMIND_MINUTES)
                key = f"{ev['uid']}|{ev['start'].isoformat()}"
                if now < remind_at or now >= ev["start"]:
                    continue
                if state["sent"].get(key):
                    continue
                s = ev["start"].astimezone(TZ)
                e = ev["end"].astimezone(TZ)
                mins_left = int((ev["start"] - now).total_seconds() // 60)
                broadcast(
                    f"⏰ *{ev['summary']}*\n"
                    f"📚 {ev['calendar']} · {s:%H:%M}–{e:%H:%M}\n"
                    f"starts in *{mins_left} min* — get ready!"
                )
                state["sent"][key] = True
                save_state(state)
                log.info("reminded: %s (%s)", ev["summary"], ev["calendar"])
        except Exception:
            log.exception("reminder loop error")
        time.sleep(30)


# ---------------------------------------------------------------- health ---

class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


def main():
    if not CALENDAR_URLS:
        log.warning("CALENDAR_URLS is empty - nothing to remind about")
    threading.Thread(target=poll_commands, daemon=True).start()
    threading.Thread(target=reminder_loop, daemon=True).start()
    log.info("health server on :%s, reminders every 30 s", PORT)
    HTTPServer(("0.0.0.0", PORT), Health).serve_forever()


if __name__ == "__main__":
    main()
