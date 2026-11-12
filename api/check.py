"""
Reminder engine (Vercel + cron-job.org, pinged every 5 min)

Fires:
  1. Task reminders   - REMIND_MINUTES before every calendar event
  2. Sleep guardian   - 00:00 nightly (skip-aware later if needed)
  3. Morning briefing - 07:45 daily: schedule + weather + prayers + backlog

State lives in state.json in this GitHub repo (via Contents API).

Env vars:
  TELEGRAM_TOKEN, CHAT_ID, CALENDAR_URLS (JSON), GH_TOKEN
Optional:
  REMIND_MINUTES (30), GH_REPO (YoYo-Python/ReminderBot), TIMEZONE (Africa/Cairo)
"""

import base64
import json
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from icalendar import Calendar
from flask import Flask, jsonify, request

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
        "message": "bot: update state",
        "content": base64.b64encode(json.dumps(state).encode()).decode(),
        "branch": BRANCH,
    }
    if sha:
        body["sha"] = sha
    r = requests.put(GH_API, headers=GH_HEADERS, json=body, timeout=25)
    if r.status_code not in (200, 201):
        log.warning("state save failed: %s %s", r.status_code, r.text[:200])


def prune(state):
    cutoff = datetime.now(TZ) - timedelta(days=7)
    kept = {}
    for k, v in state.items():
        if "|" not in k:
            kept[k] = v  # named keys like "backlog"
            continue
        try:
            if datetime.fromisoformat(k.split("|", 1)[1]) >= cutoff:
                kept[k] = v
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


# ------------------------------------------------------ morning briefing ---

WEATHER_CODES = {
    0: "clear ☀️", 1: "mostly clear 🌤", 2: "partly cloudy ⛅", 3: "overcast ☁️",
    45: "fog 🌫", 48: "fog 🌫",
    51: "light drizzle 🌦", 53: "drizzle 🌦", 55: "drizzle 🌦",
    61: "light rain 🌧", 63: "rain 🌧", 65: "heavy rain 🌧",
    71: "light snow 🌨", 73: "snow 🌨", 75: "snow 🌨",
    80: "showers 🌦", 81: "showers 🌦", 82: "heavy showers 🌧",
    95: "thunderstorm ⛈", 96: "thunderstorm ⛈", 99: "thunderstorm ⛈",
}


def weather_line():
    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": "30.0444", "longitude": "31.2357",
                "current_weather": "true",
                "daily": "temperature_2m_max,temperature_2m_min,weathercode",
                "timezone": "Africa/Cairo",
            },
            timeout=15,
        )
        d = r.json()
        cur, day = d["current_weather"], d["daily"]
        desc = WEATHER_CODES.get(int(cur["weathercode"]), "—")
        return (
            f"🌤 Cairo: {cur['temperature']:.0f}°C now · "
            f"{day['temperature_2m_min'][0]:.0f}–{day['temperature_2m_max'][0]:.0f}°C today · {desc}"
        )
    except Exception:
        return None


def prayers_line():
    try:
        r = requests.get(
            "https://api.aladhan.com/v1/timingsByCity",
            params={"city": "Cairo", "country": "Egypt", "method": 5},
            timeout=15,
        )
        t = r.json()["data"]["timings"]
        clean = lambda x: x.split(" ")[0]
        return (
            f"🕌 Fajr {clean(t['Fajr'])} · Dhuhr {clean(t['Dhuhr'])} · Asr {clean(t['Asr'])} · "
            f"Maghrib {clean(t['Maghrib'])} · Isha {clean(t['Isha'])}"
        )
    except Exception:
        return None


def briefing(now, state, events):
    today = now.date()
    todays = [e for e in events if e["start"].astimezone(TZ).date() == today]
    lines = [f"☀️ *Good morning! Today — {today:%a %d %b}*"]
    if todays:
        for e in todays:
            s, e2 = e["start"].astimezone(TZ), e["end"].astimezone(TZ)
            lines.append(f"• {s:%H:%M}–{e2:%H:%M} — {e['summary']} _({e['calendar']})_")
    else:
        lines.append("🌙 Nothing scheduled — free day.")
    w, p = weather_line(), prayers_line()
    if w:
        lines.append(w)
    if p:
        lines.append(p)
    b = state.get("backlog", {"left": 21})
    lines.append(f"📚 GIT backlog: *{b.get('left', 21)}* lectures left")
    return "\n".join(lines)


# ------------------------------------------------------------------ logic --

def run_check(test=None):
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
        if send(
            f"⏰ *{ev['summary']}*\n"
            f"📚 {ev['calendar']} · {s:%H:%M}–{e:%H:%M}\n"
            f"starts in *{mins_left} min* — get ready!"
        ):
            state[key] = True
            fired.append(ev["summary"])

    changed = bool(fired)

    # sleep guardian: fires once, between 00:00 and 00:45
    if test == "sleep":
        if send("🌙 *TEST — Sleep window in 30 min* — start winding down.\nScreens down, lights low. (12:30 AM target 💤)"):
            fired.append("(sleep test)")
    else:
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        key = f"sleep|{now.date().isoformat()}"
        if midnight <= now < midnight + timedelta(minutes=45) and not state.get(key):
            if send("🌙 *Sleep window in 30 min* — start winding down.\nScreens down, lights low. (12:30 AM target 💤)"):
                state[key] = True
                changed = True

    # morning briefing: fires once, between 07:45 and 10:00
    morn = now.replace(hour=7, minute=45, second=0, microsecond=0)
    key = f"brief|{now.date().isoformat()}"
    if morn <= now < morn + timedelta(hours=2, minutes=15) and not state.get(key):
        if send(briefing(now, state, events)):
            state[key] = True
            changed = True

    if changed:
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
        result = run_check(test=request.args.get("test"))
    except Exception:
        log.exception("check failed")
        return jsonify({"error": "check failed"}), 500
    return jsonify(result)
