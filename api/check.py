"""
Telegram Task Reminder - serverless edition (Vercel + cron-job.org)

Every ping to /api/check:
  1. fetches iCloud public calendar feeds (CALENDAR_URLS env, JSON)
  2. finds events whose reminder moment (start - REMIND_MINUTES) has arrived
  3. sends a Telegram message to CHAT_ID
  4. persists "already sent" state to state.json in this GitHub repo
     (via GitHub Contents API, using GH_TOKEN)

Env vars required:
  TELEGRAM_TOKEN  - BotFather token
  CHAT_ID         - your Telegram chat id
  CALENDAR_URLS   - JSON: {"Calendar Name": "https://...ics", ...}
  GH_TOKEN        - GitHub PAT with Contents: Read and write on this repo
Optional:
  REMIND_MINUTES  - default 30
  GH_REPO         - default "YoYo-Python/ReminderBot"
  TIMEZONE        - default "Africa/Cairo"
"""

import base64
import json
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from icalendar import Calendar
from flask import Flask, jsonify

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("check")

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
CALENDAR_URLS = json.loads(os.environ.get("CALENDAR_URLS", "{}"))
REMIND_MINUTES = int(os.environ.get("REMIND_MINUTES", "30"))
GH_TOKEN = os.environ["GH_TOKEN"]
GH_REPO = os.environ.get("GH_REPO", "YoYo-Python/ReminderBot")
STATE_PATH = "state.json"
BRANCH = "main"
TZ = ZoneInfo(os.environ.get("TIMEZONE", "Africa/Cairo"))

GH_API = f"https://api.github.com/repos/{GH_REPO}/contents/{STATE_PATH}"
GH_HEADERS = {
    "Authorization": f"Bearer {GH_TOKEN}",
    "Accept": "application/vnd.github+json",
}

app = Flask(__name__)


# ------------------------------------------------------------------ state --

def load_state():
    """Read sent-flags from state.json in the repo. 404 -> empty."""
    try:
        r = requests.get(GH_API, headers=GH_HEADERS, timeout=25)
        if r.status_code == 200:
            content = base64.b64decode(r.json()["content"]).decode()
            return json.loads(content), r.json()["sha"]
    except Exception:
        log.exception("state read failed")
    return {}, None


def save_state(state, sha):
    body = {
        "message": f"bot: sent {len(state)} total reminders",
        "content": base64.b64encode(json.dumps(state).encode()).decode(),
        "branch": BRANCH,
    }
    if sha:
        body["sha"] = sha
    r = requests.put(GH_API, headers=GH_HEADERS, json=body, timeout=25)
    if r.status_code not in (200, 201):
        log.warning("state save failed: %s %s", r.status_code, r.text[:200])


def prune(state):
    """Drop sent-flags older than 7 days past their event start."""
    cutoff = datetime.now(TZ) - timedelta(days=7)
    kept = {}
    for k in state:
        try:
            if datetime.fromisoformat(k.split("|", 1)[1]) >= cutoff:
                kept[k] = True
        except Exception:
            pass
    return kept


# --------------------------------------------------------------- telegram --

def send(text):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=25,
        )
        return r.json().get("ok", False)
    except Exception:
        log.exception("telegram send failed")
        return False


# ------------------------------------------------------------- calendars ---

def fetch_events():
    now = datetime.now(TZ)
    horizon = now + timedelta(hours=36)
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
            if not isinstance(start, datetime):  # skip all-day events
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


# ------------------------------------------------------------------ logic --

def run_check():
    now = datetime.now(TZ)
    state, sha = load_state()
    state = prune(state)
    events = fetch_events()

    fired = []
    for ev in events:
        key = f"{ev['uid']}|{ev['start'].isoformat()}"
        if state.get(key):
            continue
        remind_at = ev["start"] - timedelta(minutes=REMIND_MINUTES)
        if not (remind_at <= now < ev["start"]):
            continue
        s = ev["start"].astimezone(TZ)
        e = ev["end"].astimezone(TZ)
        mins_left = max(1, int((ev["start"] - now).total_seconds() // 60))
        ok = send(
            f"⏰ *{ev['summary']}*\n"
            f"📚 {ev['calendar']} · {s:%H:%M}–{e:%H:%M}\n"
            f"starts in *{mins_left} min* — get ready!"
        )
        if ok:
            state[key] = True
            fired.append(ev["summary"])

    if fired:
        save_state(state, sha)

    return {
        "checked": len(events),
        "sent": fired,
        "known_sent": len(state),
    }


@app.route("/")
@app.route("/api/check")
def check():
    try:
        result = run_check()
    except Exception:
        log.exception("check failed")
        return jsonify({"error": "check failed"}), 500
    return jsonify(result)
