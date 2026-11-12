# Telegram Task Reminder Bot

Messages you **30 minutes before every event** in your iCloud calendars.
Runs on a free cloud host (Render), reads your calendars directly from iCloud —
your Mac doesn't need to be on.

## One-time setup

### 1. Create the bot (2 min)
1. Open Telegram → message **@BotFather**
2. `/newbot` → pick a name and username
3. Copy the **token** (`123456:ABC-DEF...`)

### 2. Publish your calendars as public feeds (5 min)
1. Go to **icloud.com** → Calendar
2. Right-click each calendar you want reminders from (**GIT**, **Community**,
   **Forensic Medicine**, **Home**, ...) → *Share Calendar* → enable
   **Public Calendar** → copy the link (starts with `webcal://`)
3. Change `webcal://` → `https://` — that's your feed URL
   (they're read-only, nobody can see your events unless they have the link)

### 3. Deploy to Render (free, 10 min)
1. Push this folder to a GitHub repo (or use Render's "Direct deploy")
2. render.com → **New → Web Service** → connect the repo
   - Runtime: **Python 3**
   - Build command: `pip install -r requirements.txt`
   - Start command: `python bot.py`
   - Instance type: **Free**
3. Add **Environment variables**:
   | Key | Value |
   |---|---|
   | `TELEGRAM_TOKEN` | your BotFather token |
   | `CALENDAR_URLS` | `{"GIT": "https://...ics", "Community": "https://...ics", "Forensic Medicine": "https://...ics", "Home": "https://...ics"}` |
   | `REMIND_MINUTES` | `30` |

### 4. Keep the free tier awake
Free Render services sleep after 15 min of no traffic.
1. cron-job.org (free) → create a job hitting your Render URL every 10 min
2. Done — the service stays warm.

### 5. Register yourself
Message your bot **/start** in Telegram. It saves your chat ID and replies
with it. Send **/tasks** to see the next 5 upcoming tasks as a test.

## Local test (optional, before deploying)
```bash
cd telegram-task-bot
pip install -r requirements.txt
export TELEGRAM_TOKEN="123456:ABC..."
export CALENDAR_URLS='{"Home": "https://...ics"}'
python bot.py
```

## Notes
- Reminders fire for events starting within the next 36 h.
- The bot never sends duplicate reminders (state is deduped per event+start time).
- iCloud public feeds update within ~1-2 min of you adding an event,
  so last-minute calendar edits are picked up.
# ReminderBot
