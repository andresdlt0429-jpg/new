# CRM Pipeline Board

A lightweight CRM that turns your Google Calendar into a lead pipeline. You
color-code call/lead events on your calendar, and this app syncs them onto a
Kanban board — grouped into stages by event color. Moving a card on the
board pushes the matching color back to the calendar event, so the board and
your calendar always agree. Optional Discord webhook posts a message
whenever a lead is created or changes stage.

## How the color → stage matching works

Google Calendar events can be assigned one of 11 fixed colors. Pick a color
whenever you create a call/lead event, and this app classifies it into a
stage automatically:

| Stage | Default color |
|---|---|
| New Lead | Blueberry (dark blue) |
| Demo Call Scheduled | Peacock (teal) |
| Demo Call – Thinking It Over | Banana (yellow) |
| Demo Call – Free Trial | Flamingo (pink) |
| Closed Won | Basil (dark green) |
| Ghosted / No-Show | Tomato (red) |
| Closed Lost | Graphite (gray) |

Events with no color set (Google's calendar-default color) or a color not
in this list are ignored — they won't show up as leads. You can remap any
stage to a different color any time from the **Settings** page.

## 1. Set up Google OAuth credentials (one-time)

1. Go to https://console.cloud.google.com/apis/credentials and create (or
   pick) a project.
2. **Enable the API**: APIs & Services → Library → search "Google Calendar
   API" → Enable.
3. **Configure the consent screen**: APIs & Services → OAuth consent screen.
   Choose "External", fill in the required fields, and add your own Google
   account under "Test users" (this keeps it private to you without needing
   Google's app review).
4. **Create credentials**: APIs & Services → Credentials → Create
   Credentials → OAuth client ID → Application type "Web application".
   - Add an Authorized redirect URI: `http://localhost:3000/auth/google/callback`
     (match the port/host if you change `PORT`/`BASE_URL`).
5. Copy the generated **Client ID** and **Client Secret**.

## 2. Configure the app

```bash
cd crm
cp .env.example .env
```

Edit `.env` and fill in `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and a
random `SESSION_SECRET`.

## 3. Install and run

```bash
npm install
npm start        # or: npm run dev  (auto-restarts on file changes)
```

Open http://localhost:3000, go to **Settings**, click **Connect Google
Calendar**, and sign in. Pick which calendar to sync (defaults to your
primary calendar), and hit **Save Settings**.

Back on the board, click **Sync Now** to pull in any already-colored events.
After that, the app auto-syncs on the interval you set in Settings (every 5
minutes by default; requires a server restart to pick up an interval
change).

## 4. (Optional) Discord notifications

1. In Discord: **Server Settings → Integrations → Webhooks → New Webhook**,
   pick a channel, and **Copy Webhook URL**.
2. Paste it into the Discord section of the Settings page and click **Send
   test message** to confirm it works.

You'll now get a Discord message whenever a new colored lead event is
found, and whenever a lead moves between stages (from the calendar or by
dragging its card on the board).

## Using the board

- **Drag a card** between columns to change its stage — this also recolors
  the linked calendar event to match.
- **Click a card** to edit its name/email/phone/notes, or delete it (this
  only removes the CRM record, not the calendar event).
- **+ Add Lead** creates a lead directly in the CRM with no calendar event
  attached yet.
- Leads created from calendar sync keep a link back to the original event
  (the "Calendar ↗" link on the card).

## Project structure

```
crm/
  src/
    server.js        Express app + auto-sync scheduler
    db.js             SQLite schema + settings key/value store
    stages.js         Google's 11 event colors + default stage mapping
    googleAuth.js      OAuth2 flow + token storage/refresh
    calendarSync.js    Pulls calendar events, classifies by color, upserts leads
    discord.js          Webhook notifications
    routes/            leads.js, settings.js, auth.js
  public/              Kanban board + settings UI (vanilla HTML/JS, no build step)
```

Data is stored locally in `crm/crm.db` (SQLite). This is a single-user app —
there's no login screen beyond the Google OAuth connection.
