# ReminderBot — Telegram task reminders (Vercel + cron-job.org)

Messages **30 minutes before every event** in your iCloud public calendars.
Free, no credit card anywhere in the stack.

## Architecture
```
cron-job.org (every 5 min, free)
      │  GET
      ▼
Vercel /api/check  (Hobby plan, free, no card)
      │  reads iCloud webcal feeds
      │  sends Telegram message if a task's -30 min mark arrived
      ▼
state.json in THIS repo (GitHub API) = the database
```

## Setup

### 1. Vercel (no card)
1. vercel.com → sign in with GitHub → **Add New → Project**
2. Import `YoYo-Python/ReminderBot`
3. Framework preset: **Other** (it auto-detects the Python API)
4. Add **Environment Variables**:

| Key | Value |
|---|---|
| `TELEGRAM_TOKEN` | your BotFather token |
| `CHAT_ID` | `1816485344` |
| `CALENDAR_URLS` | JSON of feed URLs, e.g. `{"GIT": "https://...ics", "Community": "https://...ics", "Forensic Medicine": "https://...ics", "Home": "https://...ics"}` |
| `GH_TOKEN` | GitHub PAT — see step 2 |
| `REMIND_MINUTES` | `30` |

5. **Deploy**

### 2. GitHub token (for the state file — no card)
1. github.com → Settings → Developer settings → **Fine-grained personal access tokens** → Generate new
2. Repository access: **Only select repositories** → `ReminderBot`
3. Permissions: **Contents → Read and write**
4. Paste the token as the `GH_TOKEN` env var in Vercel, then **Redeploy**

### 3. cron-job.org (no card)
1. cron-job.org → create cron job
2. URL: `https://YOUR-PROJECT.vercel.app/api/check`
3. Schedule: **every 5 minutes**

### 4. Test
Open `https://YOUR-PROJECT.vercel.app/api/check` in a browser — you should see
`{"checked": N, "sent": [], "known_sent": M}`.
Send `/tasks`… not needed — this version has no commands; just create an
event ~35 min in the future and wait for the buzz.

## Notes
- iCloud public feeds update within ~1–2 min of adding an event.
- `state.json` commits appear in the repo whenever a reminder fires.
- The old `bot.py` (Render version) is kept for reference but is not used here.
